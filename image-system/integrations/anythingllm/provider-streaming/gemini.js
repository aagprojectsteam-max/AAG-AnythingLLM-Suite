const { SerialToolAssembly, requireProtocol, argumentsObject } = require("./tool-contract.js");
const OpenAI = require("openai");
const Provider = require("./ai-provider.js");
const { RetryError } = require("../error.js");
const { safeJsonParse } = require("../../../http");
const { v4 } = require("uuid");

/**
 * The agent provider for the Gemini provider.
 * We wrap Gemini in UnTooled because its tool-calling is not supported via the dedicated OpenAI API.
 */
class GeminiProvider extends Provider {
  model;

  constructor(config = {}) {
    const { model = "gemini-2.0-flash-lite" } = config;
    super();
    this.providerTag = "gemini";
    this.className = "GeminiProvider";
    const client = new OpenAI({
      baseURL: "https://generativelanguage.googleapis.com/v1beta/openai/",
      apiKey: process.env.GEMINI_API_KEY,
      fetch: async (url, init) => {
        const res = await globalThis.fetch(url, init);
        if (!res.ok) {
          const cloned = res.clone();
          const text = await cloned.text().catch(() => "(unreadable)");
          this.providerLog(
            `[Gemini.fetch] ${res.status} from ${typeof url === "string" ? url : url?.toString()}\n` +
              `  Response body: ${text}`
          );
          try {
            const json = safeJsonParse(text, {});
            const errorObj = Array.isArray(json) ? json[0] : json;
            this._lastErrorMessage = errorObj?.error?.message || null;
          } catch {
            this._lastErrorMessage = text?.slice(0, 200) || null;
          }
        }
        return res;
      },
    });

    this._client = client;
    this.model = model;
    this.verbose = true;
  }

  get client() {
    return this._client;
  }

  /**
   * Whether this provider supports agent streaming.
   * - Tool call streaming results in a 400/503 error for all non-gemini models
   * using the compatible v1beta/openai/ endpoint
   * @returns {boolean}
   */
  get supportsAgentStreaming() {
    if (!this.model.startsWith("gemini")) {
      this.providerLog(
        `Gemini: ${this.model} does not support tool call streaming.`
      );
      return false;
    }
    return true;
  }

  get supportsToolCalling() {
    if (!this.model.startsWith("gemini")) return false;
    return true;
  }

  /**
   * Whether this provider supports native OpenAI-compatible tool calling.
   * - Gemini only supports tool calling for Gemini models.
   * @returns {boolean}
   */
  supportsNativeToolCalling() {
    if (this.optsOutOfNativeToolCallingViaEnv(this.providerTag)) return false;
    return this.supportsToolCalling;
  }

  /**
   * Gemini specifcally will throw an error if the tool call's function name
   * starts with a non-alpha character. So we need to prefix the function names
   * with a valid prefix to ensure they are always valid and then strip them back
   * so they may properly be used in the tool call.
   *
   * So for all tools, we force the prefix to be gtc__ to avoid issues
   * Agent flows are already prefixed with flow__ but since we strip the prefix
   * anyway pre and post-reply, we do it anyway to ensure consistency across all tools.
   *
   * This specifically impacts the custom Agent Skills since they can be a short alphanumeric
   * and cant definitely start with a number. eg: '12xdaya31bas' -> invalid in gemini tools.
   *
   * Even if the tool is never called, if it is in the `tools` array and this prefix
   * patch is not applied, gemini will throw an error.
   *
   * This is undocumented by google, but it is the only way to ensure that tool calls
   * are valid.
   *
   * @param {string} functionName - The name of the function to prefix.
   * @param {'add' | 'strip'} action - The action to take.
   * @returns {string} The prefixed function name.
   * @returns {string} The prefix to use for tool call ids.
   */
  prefixToolCall(functionName, action = "add") {
    if (action === "add") return `gtc__${functionName}`;
    // must start with gtc__ to be valid and we only strip the first instance
    return functionName.startsWith("gtc__")
      ? functionName.split("gtc__")[1]
      : functionName;
  }

  /**
   * Format the messages to the Gemini API Responses format.
   * - Gemini has some loosely documented format for tool calls and it can change at any time.
   * - We need to map the function call to the correct id and Gemini will throw an error if it does not.
   * - Gemini requires a `thought_signature` (via `extra_content.google.thought_signature`) on function call
   *   parts in multi-turn tool conversations. This is an encrypted token Gemini attaches to every tool call
   *   it makes, and it must be passed back when sending tool results or Gemini rejects the request with a 400.
   *   See: https://ai.google.dev/gemini-api/docs/thought-signatures
   * @param {any[]} messages - The messages to format.
   * @returns {OpenAI.OpenAI.Responses.ResponseInput[]} The formatted messages.
   */
  #formatMessages(messages) {
    let formattedMessages = [];
    messages.forEach((message) => {
      if (message.role === "function") {
        // If the message does not have an originalFunctionCall we cannot
        // map it to a function call id and Gemini will throw an error.
        // so if this does not carry over - log and skip
        if (!message.hasOwnProperty("originalFunctionCall")) {
          this.providerLog(
            "[Gemini.#formatMessages]: message did not pass back the originalFunctionCall. We need this to map the function call to the correct id.",
            { message: JSON.stringify(message, null, 2) }
          );
          return;
        }

        const prefixedName = this.prefixToolCall(
          message.originalFunctionCall.name,
          "add"
        );
        formattedMessages.push(
          {
            role: "assistant",
            content: "",
            tool_calls: [
              {
                type: "function",
                ...(message.originalFunctionCall.extra_content
                  ? {
                      extra_content: message.originalFunctionCall.extra_content,
                    }
                  : {}),
                function: {
                  arguments: JSON.stringify(
                    message.originalFunctionCall.arguments
                  ),
                  name: prefixedName,
                },
                id: message.originalFunctionCall.id,
              },
            ],
          },
          {
            role: "tool",
            tool_call_id: message.originalFunctionCall.id,
            name: prefixedName,
            content: message.content,
          }
        );
        return;
      }

      // Handle messages with attachments (images) for multimodal support
      if (message.attachments && message.attachments.length > 0) {
        const content = [{ type: "text", text: message.content }];
        for (const attachment of message.attachments) {
          content.push({
            type: "image_url",
            image_url: {
              url: attachment.contentString,
            },
          });
        }
        formattedMessages.push({
          role: message.role,
          content,
        });
        return;
      }

      formattedMessages.push({
        role: message.role,
        content: message.content,
      });
    });

    return formattedMessages;
  }

  #logAPIError(error) {
    const allProps = {};
    for (const key of Object.getOwnPropertyNames(error)) {
      try {
        const val = error[key];
        if (typeof val === "function") continue;
        allProps[key] = val;
      } catch {
        // ignore
      }
    }
    for (const key of [
      "status",
      "code",
      "type",
      "error",
      "body",
      "param",
      "headers",
      "cause",
      "response",
      "request_id",
    ]) {
      try {
        if (error[key] !== undefined) allProps[key] = error[key];
      } catch {
        // ignore
      }
    }
  }

  #formatFunctions(functions) {
    return functions.map((func) => ({
      type: "function",
      function: {
        name: this.prefixToolCall(func.name, "add"),
        description: func.description,
        parameters: func.parameters,
      },
    }));
  }

  async stream(messages, functions = [], eventHandler = null) {
    if (!this.supportsToolCalling)
      throw new Error(`Gemini: ${this.model} does not support tool calling.`);
    this.providerLog("Gemini.stream - will process this chat completion.");
    this.resetUsage();

    try {
      const msgUUID = v4();
      /** @type {OpenAI.OpenAI.Chat.ChatCompletion} */
      const response = await this.client.chat.completions.create({
        model: this.model,
        messages: this.#formatMessages(messages),
        stream: true,
        stream_options: { include_usage: true },
        ...(Array.isArray(functions) && functions?.length > 0
          ? {
              tools: this.#formatFunctions(functions),
              tool_choice: "auto",
              // AIbitat runs one tool per turn; parallel calls cause a 400
              // on the next request due to a tool call/result count mismatch.
              parallel_tool_calls: false,
            }
          : {}),
      });

      const tools = new SerialToolAssembly();
      let content = "", finished = false;
      for await (const chunk of response) {
        requireProtocol(!chunk.error, "PROVIDER_ERROR", chunk.error?.message || "Provider stream error.");
        if (chunk.usage) this.recordUsage(chunk.usage);
        const choice = chunk.choices?.[0];
        if (!choice) continue;
        const value = choice.delta || {};
        requireProtocol(!finished || (!value.content && !value.tool_calls?.length), "PROVIDER_EVENT_AFTER_END", "Provider sent content after completion.");
        if (value.content) {
          content += value.content;
          eventHandler?.("reportStreamEvent", {type:"textResponseChunk",uuid:msgUUID,content:value.content});
        }
        for (const item of value.tool_calls || []) {
          const index = item.index ?? 0;
          requireProtocol(Number.isInteger(index) && index >= 0, "TOOL_ID_INVALID", "Invalid tool index.");
          let call = tools.calls.get(index);
          if (!call) call = tools.start(index, {id:item.id,name:this.prefixToolCall(item.function?.name || "", "strip"),extra_content:item.extra_content});
          else {
            requireProtocol(!item.id || item.id===call.id,"TOOL_ID_CONFLICT","Tool call ID changed.");
            requireProtocol(!item.function?.name || this.prefixToolCall(item.function.name,"strip")===call.name,"TOOL_ID_CONFLICT","Tool name changed.");
            if(item.extra_content) {
              requireProtocol(!call.extra_content || JSON.stringify(call.extra_content)===JSON.stringify(item.extra_content),"TOOL_ID_CONFLICT","Provider tool metadata changed.");
              call.extra_content=item.extra_content;
            }
          }
          if (item.function?.arguments !== undefined) tools.delta(index,item.function.arguments);
          eventHandler?.("reportStreamEvent", {type:"toolCallInvocation",uuid:`${msgUUID}:tool_call_invocation`,content:`Assembling Tool Call: ${call.name}(${call.raw})`});
        }
        if (choice.finish_reason) {
          requireProtocol(["stop","tool_calls"].includes(choice.finish_reason),"PROVIDER_STREAM_INCOMPLETE","Provider response stopped before successful completion.");
          if (!finished) for (const key of tools.calls.keys()) tools.complete(key);
          finished=true;
        }
      }
      return {textResponse:content,functionCall:tools.finish(finished),cost:this.getCost(),uuid:msgUUID};
    } catch (error) {
      this.#logAPIError(error);
      const errorMsg = this._lastErrorMessage
        ? `Gemini error: ${this._lastErrorMessage}`
        : error.message;
      this._lastErrorMessage = null;
      if (error instanceof OpenAI.AuthenticationError) throw error;
      if (
        error instanceof OpenAI.RateLimitError ||
        error instanceof OpenAI.InternalServerError ||
        error instanceof OpenAI.APIError // Also will catch AuthenticationError!!!
      ) {
        throw new RetryError(errorMsg);
      }

      throw error;
    }
  }

  /**
   * Create a completion based on the received messages.
   *
   * @param messages A list of messages to send to the Gemini API.
   * @param functions
   * @returns The completion.
   */
  async complete(messages, functions = []) {
    if (!this.supportsToolCalling)
      throw new Error(`Gemini: ${this.model} does not support tool calling.`);
    this.providerLog("Gemini.complete - will process this chat completion.");
    this.resetUsage();

    try {
      const response = await this.client.chat.completions.create({
        model: this.model,
        stream: false,
        messages: this.#formatMessages(messages),
        ...(Array.isArray(functions) && functions?.length > 0
          ? {
              tools: this.#formatFunctions(functions),
              tool_choice: "auto",
              // AIbitat runs one tool per turn; parallel calls cause a 400
              // on the next request due to a tool call/result count mismatch.
              parallel_tool_calls: false,
            }
          : {}),
      });

      if (response.usage) this.recordUsage(response.usage);

      /** @type {OpenAI.OpenAI.Chat.ChatCompletionMessage} */
      const completion = response.choices[0].message;
      const cost = this.getCost(response.usage);
      if (completion?.tool_calls?.length > 0) {
        const toolCall = completion.tool_calls[0];
        let functionArgs = argumentsObject(toolCall.function.arguments);
        return {
          textResponse: null,
          functionCall: {
            name: this.prefixToolCall(toolCall.function.name, "strip"),
            arguments: functionArgs,
            id: toolCall.id,
            // Preserve Gemini's thought_signature so it can be passed back in #formatMessages
            extra_content: toolCall.extra_content ?? null,
          },
          cost,
          usage: this.getUsage(),
        };
      }

      return {
        textResponse: completion.content,
        cost,
        usage: this.getUsage(),
      };
    } catch (error) {
      this.#logAPIError(error);
      const errorMsg = this._lastErrorMessage
        ? `Gemini error: ${this._lastErrorMessage}`
        : error.message;
      this._lastErrorMessage = null;
      // If invalid Auth error we need to abort because no amount of waiting
      // will make auth better.
      if (error instanceof OpenAI.AuthenticationError) throw error;

      if (
        error instanceof OpenAI.RateLimitError ||
        error instanceof OpenAI.InternalServerError ||
        error instanceof OpenAI.APIError // Also will catch AuthenticationError!!!
      ) {
        throw new RetryError(errorMsg);
      }

      throw error;
    }
  }

  /**
   * Get the cost of the completion.
   *
   * @param _usage The completion to get the cost for.
   * @returns The cost of the completion.
   */
  getCost(_usage) {
    return 0;
  }
}

module.exports = GeminiProvider;
