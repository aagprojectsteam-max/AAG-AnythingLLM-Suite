const { classifyError, requestSummary, StreamTrace, decodeAnthropic } = require('./anthropic-transport.js');
const { SerialToolAssembly, requireProtocol, argumentsObject } = require("./tool-contract.js");
const Anthropic = require("@anthropic-ai/sdk");
const { AnthropicLLM } = require("../../../AiProviders/anthropic");
const { RetryError } = require("../error.js");
const Provider = require("./ai-provider.js");
const { v4 } = require("uuid");
const { safeJsonParse } = require("../../../http");
const { getAnythingLLMUserAgent } = require("../../../../endpoints/utils");
const { dereferenceSchema } = require("./helpers/dereferenceSchema");

/**
 * The agent provider for the Anthropic API.
 * By default, the model is set to 'claude-sonnet-4-6'.
 */
class AnthropicProvider extends Provider {
  model;
  maxTokens = null;

  constructor(config = {}) {
    const {
      options = {
        apiKey: process.env.ANTHROPIC_API_KEY,
        defaultHeaders: {
          "User-Agent": getAnythingLLMUserAgent(),
        },
      },
      model = "claude-sonnet-4-6",
    } = config;

    const client = new Anthropic(options);

    super(client);
    this.providerTag = "anthropic";
    this.model = model;
  }

  /**
   * Whether this provider supports native OpenAI-compatible tool calling.
   * - Anthropic always supports tool calling.
   * @returns {boolean}
   */
  supportsNativeToolCalling() {
    return true;
  }

  /**
   * Fetches the maximum number of tokens the model should generate in its response.
   * This varies per model but will fallback to 4096 if the model is not found.
   * @returns {Promise<number>} The maximum output tokens limit for API calls.
   */
  async assertModelMaxTokens() {
    if (this.maxTokens) return this.maxTokens;
    this.maxTokens = await AnthropicLLM.fetchModelMaxTokens(this.model);
    return this.maxTokens;
  }

  /**
   * Parses the cache control ENV variable
   *
   * If caching is enabled, we can pass less than 1024 tokens and Anthropic will just
   * ignore it unless it is above the model's minimum. Since this feature is opt-in
   * we can safely assume that if caching is enabled that we should just pass the content as is.
   * https://docs.claude.com/en/docs/build-with-claude/prompt-caching#cache-limitations
   *
   * @param {string} value - The ENV value (5m or 1h)
   * @returns {null|{type: "ephemeral", ttl: "5m" | "1h"}} Cache control configuration
   */
  get cacheControl() {
    // Store result in instance variable to avoid recalculating
    if (this._cacheControl) return this._cacheControl;

    if (!process.env.ANTHROPIC_CACHE_CONTROL) this._cacheControl = null;
    else {
      const normalized =
        process.env.ANTHROPIC_CACHE_CONTROL.toLowerCase().trim();
      if (["5m", "1h"].includes(normalized))
        this._cacheControl = { type: "ephemeral", ttl: normalized };
      else this._cacheControl = null;
    }
    return this._cacheControl;
  }

  get supportsAgentStreaming() {
    return true;
  }

  /**
   * Builds system parameter with cache control if applicable
   * @param {string} systemContent - The system prompt content
   * @returns {string|array} System parameter for API call
   */
  #buildSystemPrompt(systemContent) {
    if (!systemContent || !this.cacheControl) return systemContent;
    return [
      {
        type: "text",
        text: systemContent,
        cache_control: this.cacheControl,
      },
    ];
  }

  /**
   * Parse a data URL into media type and base64 data
   * @param {string} dataUrl - Data URL like "data:image/jpeg;base64,/9j/..."
   * @returns {{mediaType: string, data: string}|null}
   */
  #parseDataUrl(dataUrl) {
    if (!dataUrl || !dataUrl.startsWith("data:")) return null;
    const matches = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
    if (!matches) return null;
    return { mediaType: matches[1], data: matches[2] };
  }

  #prepareMessages(messages = []) {
    // Extract system prompt and filter out any system messages from the main chat.
    let systemPrompt =
      "You are a helpful ai assistant who can assist the user and use tools available to help answer the users prompts and questions.";
    const chatMessages = messages.filter((msg) => {
      if (msg.role === "system") {
        systemPrompt = msg.content;
        return false;
      }
      return true;
    });

    const processedMessages = chatMessages.reduce(
      (processedMessages, message, index) => {
        // Normalize `function` role to Anthropic's `tool_result` format.
        if (message.role === "function") {
          const prevMessage = chatMessages[index - 1];
          if (prevMessage?.role === "assistant") {
            const toolUse = prevMessage.content.find(
              (item) => item.type === "tool_use"
            );
            if (toolUse) {
              processedMessages.push({
                role: "user",
                content: [
                  {
                    type: "tool_result",
                    tool_use_id: toolUse.id,
                    content: message.content
                      ? String(message.content)
                      : "Tool executed successfully.",
                  },
                ],
              });
            }
          }
          return processedMessages;
        }

        // Ensure message content is in array format and filter out empty text blocks.
        let content = Array.isArray(message.content)
          ? message.content
          : [{ type: "text", text: message.content }];
        content = content.filter(
          (item) =>
            item.type !== "text" || (item.text && item.text.trim().length > 0)
        );

        // Add image attachments if present (for vision/multimodal support)
        if (message.attachments && message.attachments.length > 0) {
          for (const attachment of message.attachments) {
            const parsed = this.#parseDataUrl(attachment.contentString);
            if (parsed) {
              content.push({
                type: "image",
                source: {
                  type: "base64",
                  media_type: parsed.mediaType,
                  data: parsed.data,
                },
              });
            }
          }
        }

        if (content.length === 0) return processedMessages;

        // Add a text block to assistant messages with tool use if one doesn't exist.
        if (
          message.role === "assistant" &&
          content.some((item) => item.type === "tool_use") &&
          !content.some((item) => item.type === "text")
        ) {
          content.unshift({
            type: "text",
            text: "I'll use a tool to help answer this question.",
          });
        }

        const lastMessage = processedMessages[processedMessages.length - 1];
        if (lastMessage && lastMessage.role === message.role) {
          // Merge consecutive messages from the same role.
          lastMessage.content.push(...content);
        } else {
          // Don't pass attachments to the final message object
          const { attachments: _, ...restOfMessage } = message;
          processedMessages.push({ ...restOfMessage, content });
        }

        return processedMessages;
      },
      []
    );

    // The first message must be from the user.
    if (processedMessages.length > 0 && processedMessages[0].role !== "user") {
      processedMessages.shift();
    }

    return [systemPrompt, processedMessages];
  }

  // Anthropic does not use the regular schema for functions so here we need to ensure it is in there specific format
  // so that the call can run correctly.
  #formatFunctions(functions = []) {
    return functions.map((func) => {
      const { name, description, parameters, required } = func;
      // Some MCP tools (e.g. Pydantic v2 nested models) describe their parameters
      // with `$ref`/`$defs`. Anthropic's `input_schema` does not resolve local
      // references, so we inline them here - otherwise the dangling pointer crashes
      // the tool call. Flat schemas are left unchanged. See issue #3938.
      const { type, properties } = dereferenceSchema(parameters);
      return {
        name,
        description,
        input_schema: {
          type,
          properties,
          required,
        },
      };
    });
  }

  /**
   * Stream a chat completion from the LLM with tool calling
   * Note: This using the Anthropic API SDK and its implementation is specific to Anthropic.
   *
   * @param {any[]} messages - The messages to send to the LLM.
   * @param {any[]} functions - The functions to use in the LLM.
   * @param {function} eventHandler - The event handler to use to report stream events.
   * @returns {Promise<{ functionCall: any, textResponse: string, uuid: string }>} - The result of the chat completion.
   */
  async stream(messages, functions = [], eventHandler = null) {
    await this.assertModelMaxTokens();
    this.resetUsage();

    const trace = new StreamTrace();
    const requestComponents = requestSummary(messages, functions);
    try {
      const msgUUID = v4();
      const [systemPrompt, chats] = this.#prepareMessages(messages);
      const pending = this.client.messages.create(
        {
          model: this.model,
          max_tokens: this.maxTokens,
          system: this.#buildSystemPrompt(systemPrompt),
          messages: chats,
          stream: true,
          ...(Array.isArray(functions) && functions?.length > 0
            ? { tools: this.#formatFunctions(functions) }
            : {}),
        },
        { headers: { "anthropic-beta": "tools-2024-04-04" } } // Required to we can use tools.
      );

      const response = await pending.asResponse();
      const tools = new SerialToolAssembly();
      const openBlocks = new Set();
      let started = false;
      const usage = {input_tokens:0,output_tokens:0};
      let textResponse = "", ended = false, stopReason = null;
      for await (const chunk of decodeAnthropic(response, trace)) {
        requireProtocol(chunk.type!=="error","PROVIDER_ERROR","Provider stream error.");
        requireProtocol(!ended || chunk.type==="ping","PROVIDER_EVENT_AFTER_END","Provider sent data after message completion.");
        if(chunk.type==="message_start") {
          requireProtocol(!started,"PROVIDER_MESSAGE_CONFLICT","Provider repeated message start.");
          started=true;usage.input_tokens=chunk.message?.usage?.input_tokens || 0;
        }
        requireProtocol(started || chunk.type==="ping","PROVIDER_MESSAGE_MISSING","Provider sent content without a message start.");
        if(chunk.type==="message_delta") {
          if(chunk.delta?.stop_reason)requireProtocol(openBlocks.size===0,"TOOL_CALL_INCOMPLETE","Provider supplied a stop reason before closing content blocks.");
          usage.output_tokens=chunk.usage?.output_tokens || usage.output_tokens;stopReason=chunk.delta?.stop_reason || stopReason;
        }
        let text = "";
        if(chunk.type==="content_block_start") {
          requireProtocol(Number.isInteger(chunk.index) && !openBlocks.has(chunk.index),"TOOL_ID_CONFLICT","Provider reused a content block.");
          openBlocks.add(chunk.index);
          if(chunk.content_block.type==="text")text=chunk.content_block.text || "";
          if(chunk.content_block.type==="tool_use") {
            const c=tools.start(chunk.index,{id:chunk.content_block.id,name:chunk.content_block.name});
            c.initial=chunk.content_block.input;
          }
        }
        if(chunk.type==="content_block_delta") {
          requireProtocol(openBlocks.has(chunk.index),"TOOL_ID_UNKNOWN","Provider sent a fragment without an open block.");
          if(chunk.delta.type==="text_delta")text=chunk.delta.text;
          if(chunk.delta.type==="input_json_delta")tools.delta(chunk.index,chunk.delta.partial_json);
        }
        if(text){textResponse+=text;eventHandler?.("reportStreamEvent",{type:"textResponseChunk",uuid:msgUUID,content:text});}
        if(chunk.type==="content_block_stop") {
          requireProtocol(openBlocks.delete(chunk.index),"PROVIDER_BLOCK_CONFLICT","Provider repeated or omitted a content block.");
          if(tools.calls.has(chunk.index)) {
            const c=tools.get(chunk.index);tools.complete(chunk.index,c.raw ? undefined : c.initial);
            if(c.raw && c.initial && Object.keys(c.initial).length)
              requireProtocol(JSON.stringify(c.initial)===JSON.stringify(c.args),"TOOL_ARGUMENTS_CONFLICT","Initial tool input conflicts with streamed input.");
          }
        }
        if(chunk.type==="message_stop") {
          requireProtocol(openBlocks.size===0,"TOOL_CALL_INCOMPLETE","Provider stopped with unfinished content blocks.");
          requireProtocol(tools.calls.size ? stopReason==="tool_use" : ["end_turn","stop_sequence"].includes(stopReason),"PROVIDER_STREAM_INCOMPLETE","Provider message stopped before successful completion.");ended=true;
        }
      }
      const functionCall=tools.finish(ended);
      this.recordUsage(usage);
      if(functionCall) messages.push({role:"assistant",content:[
        ...(textResponse ? [{type:"text",text:textResponse}] : []),
        {type:"tool_use",id:functionCall.id,name:functionCall.name,input:functionCall.arguments},
      ]});
      return {textResponse,functionCall,cost:0,uuid:msgUUID,termination:{state:"RESPONSE_COMPLETE",reason:"MESSAGE_STOP"}};
    } catch (error) {
      error = classifyError(error);
      this.providerLog?.('Anthropic request failed', {code:error.code || 'PROVIDER_ERROR', details:error.details || null, stream:trace, request_components:requestComponents});
      // If invalid Auth error we need to abort because no amount of waiting
      // will make auth better.
      if (error instanceof Anthropic.AuthenticationError) throw error;

      if (
        error instanceof Anthropic.RateLimitError ||
        error instanceof Anthropic.InternalServerError ||
        error instanceof Anthropic.APIError // Also will catch AuthenticationError!!!
      ) {
        throw new RetryError(error.message);
      }

      throw error;
    }
  }

  /**
   * Create a completion based on the received messages.
   *
   * @param messages A list of messages to send to the Anthropic API.
   * @param functions
   * @returns The completion.
   */
  async complete(messages, functions = []) {
    await this.assertModelMaxTokens();
    this.resetUsage();

    try {
      const [systemPrompt, chats] = this.#prepareMessages(messages);
      const response = await this.client.messages.create(
        {
          model: this.model,
          max_tokens: this.maxTokens,
          system: this.#buildSystemPrompt(systemPrompt),
          messages: chats,
          stream: false,
          ...(Array.isArray(functions) && functions?.length > 0
            ? { tools: this.#formatFunctions(functions) }
            : {}),
        },
        { headers: { "anthropic-beta": "tools-2024-04-04" } } // Required to we can use tools.
      );

      // Record usage from response (Anthropic uses input_tokens/output_tokens)
      if (response.usage) this.recordUsage(response.usage);

      // We know that we need to call a tool. So we are about to recurse through completions/handleExecution
      // https://docs.anthropic.com/claude/docs/tool-use#how-tool-use-works
      if (response.stop_reason === "tool_use") {
        // Get the tool call explicitly.
        const toolCall = response.content.find(
          (res) => res.type === "tool_use"
        );

        // Here we need the chain of thought the model may or may not have generated alongside the call.
        // this needs to be in a very specific format so we always ensure there is a 2-item content array
        // so that we can ensure the tool_call content is correct. For anthropic all text items must not
        // be empty, but the api will still return empty text so we need to make 100% sure text is not empty
        // or the tool call will fail.
        // wtf.
        let thought = response.content.find((res) => res.type === "text");
        thought =
          thought?.text?.length > 0
            ? {
                role: "assistant",
                content: [
                  { type: "text", text: thought.text },
                  { ...toolCall },
                ],
              }
            : {
                role: "assistant",
                content: [
                  {
                    type: "text",
                    text: `Okay, im going to use ${toolCall.name} to help me.`,
                  },
                  { ...toolCall },
                ],
              };

        // Modify messages forcefully by adding system thought so that tool_use/tool_result
        // messaging works with Anthropic's disastrous tool calling API.
        messages.push(thought);

        const functionArgs = toolCall.input;
        return {
          result: null,
          functionCall: {
            name: toolCall.name,
            arguments: functionArgs,
          },
          cost: 0,
          usage: this.getUsage(),
        };
      }

      const completion = response.content.find((msg) => msg.type === "text");
      return {
        textResponse:
          completion?.text ??
          "The model failed to complete the task and return back a valid response.",
        cost: 0,
        usage: this.getUsage(),
      };
    } catch (error) {
      error = classifyError(error);
      this.providerLog?.('Anthropic request failed', {code:error.code || 'PROVIDER_ERROR', details:error.details || null, request_components:requestSummary(messages, functions)});
      // If invalid Auth error we need to abort because no amount of waiting
      // will make auth better.
      if (error instanceof Anthropic.AuthenticationError) throw error;

      if (
        error instanceof Anthropic.RateLimitError ||
        error instanceof Anthropic.InternalServerError ||
        error instanceof Anthropic.APIError // Also will catch AuthenticationError!!!
      ) {
        throw new RetryError(error.message);
      }

      throw error;
    }
  }
}

module.exports = AnthropicProvider;
