const OpenAI = require("openai");
const Provider = require("./ai-provider.js");
const { RetryError } = require("../error.js");
const { v4 } = require("uuid");
const { decodeResponses, ResponsesAssembly, singleCall, ResponsesTrace, classifyStreamError } = require("./responses-stream.js");

/**
 * The agent provider for the OpenAI API.
 * By default, the model is set to 'gpt-4.1-nano'.
 */
class OpenAIProvider extends Provider {
  model;
  constructor(config = {}) {
    const {
      options = {
        apiKey: process.env.OPEN_AI_KEY,
      },
      model = "gpt-4.1-nano",
    } = config;

    const client = new OpenAI(options);

    super(client);

    this.providerTag = "openai";
    this.model = model;
  }

  get supportsAgentStreaming() {
    return true;
  }

  /**
   * Whether this provider supports native OpenAI-compatible tool calling.
   * - OpenAI always supports tool calling.
   * @returns {boolean}
   */
  supportsNativeToolCalling() {
    return true;
  }

  /**
   * Format the messages to the OpenAI API Responses format.
   * - If the message is our internal `function` type, then we need to map it to a function call + output format
   * - Otherwise, map it to the input text format for user, system, and assistant messages
   * - Handles attachments (images) for multimodal support
   *
   * @param {any[]} messages - The messages to format.
   * @returns {OpenAI.OpenAI.Responses.ResponseInput[]} The formatted messages.
   */
  #formatToResponsesInput(messages) {
    let formattedMessages = [];
    messages.forEach((message) => {
      if (message.role === "function") {
        // If the message does not have an originalFunctionCall we cannot
        // map it to a function call id and OpenAI will throw an error.
        // so if this does not carry over - log and skip
        if (!message.hasOwnProperty("originalFunctionCall")) {
          this.providerLog(
            "[OpenAI.#formatToResponsesInput]: message did not pass back the originalFunctionCall. We need this to map the function call to the correct id.",
            { message: JSON.stringify(message, null, 2) }
          );
          return;
        }

        formattedMessages.push(
          {
            type: "function_call",
            name: message.originalFunctionCall.name,
            call_id: message.originalFunctionCall.id,
            arguments: JSON.stringify(message.originalFunctionCall.arguments),
          },
          {
            type: "function_call_output",
            call_id: message.originalFunctionCall.id,
            output: message.content,
          }
        );
        return;
      }

      // Build content array with text and optional image attachments
      const content = [
        {
          type: message.role === "assistant" ? "output_text" : "input_text",
          text: message.content,
        },
      ];

      // Add image attachments if present (for multimodal/vision support)
      if (message.attachments && message.attachments.length > 0) {
        for (const attachment of message.attachments) {
          content.push({
            type: "input_image",
            image_url: attachment.contentString,
          });
        }
      }

      formattedMessages.push({
        role: message.role,
        content,
      });
    });

    return formattedMessages;
  }

  /**
   * Format the functions to the OpenAI API Responses format.
   *
   * @param {any[]} functions - The functions to format.
   * @returns {{
   *   type: "function",
   *   name: string,
   *   description: string,
   *   parameters: object,
   *   strict: boolean,
   * }[]} The formatted functions.
   */
  #formatFunctions(functions) {
    return functions.map((func) => ({
      type: "function",
      name: func.name,
      description: func.description,
      parameters: func.parameters,
      strict: false,
    }));
  }

  /**
   * Stream a chat completion from the LLM with tool calling
   * Note: This using the OpenAI API Responses SDK and its implementation is specific to OpenAI models.
   * Do not re-use this code for providers that do not EXACTLY implement the OpenAI API Responses SDK.
   *
   * @param {any[]} messages - The messages to send to the LLM.
   * @param {any[]} functions - The functions to use in the LLM.
   * @param {function} eventHandler - The event handler to use to report stream events.
   * @returns {Promise<{ functionCall: any, textResponse: string }>} - The result of the chat completion.
   */
  async stream(messages, functions = [], eventHandler = null) {
    this.providerLog("OpenAI.stream - will process this chat completion.");
    this.resetUsage();

    const trace = new ResponsesTrace();
    let responseStatus = null;
    const startedAt = Date.now();
    try {
      const msgUUID = v4();

      // Keep the SDK transport, credentials, retries and abort signal. Decode
      // Responses SSE here because SDK 4.95.1 dispatches event-only frames.
      const response = await this.client.responses.create({
        model: this.model,
        input: this.#formatToResponsesInput(messages),
        stream: true,
        store: false,
        parallel_tool_calls: false,
        ...(Array.isArray(functions) && functions.length
          ? { tools: this.#formatFunctions(functions) } : {}),
      }).asResponse();
      responseStatus = response.status;
      const assembly = new ResponsesAssembly({
        onText: (delta) => eventHandler?.("reportStreamEvent", {
          type: "textResponseChunk", uuid: msgUUID, content: delta,
        }),
        onTool: (call) => eventHandler?.("reportStreamEvent", {
          type: "toolCallInvocation", uuid: `${msgUUID}:tool_call_invocation`,
          content: `Assembling Tool Call: ${call.name}(${call.delta})`,
        }),
      });
      for await (const event of decodeResponses(response, {signal:this.abortSignal,trace})) {trace.event(event);assembly.push(event);}
      const result = assembly.finish({signal:this.abortSignal,trace});
      const functionCall = singleCall(result);
      if (result.usage) this.recordUsage(result.usage);
      return { termination: result.termination, textResponse: result.textResponse, functionCall,
        cost: this.getCost(), uuid: msgUUID };
    } catch (caught) {
      const error = classifyStreamError(caught, this.abortSignal);
      // Counts and protocol labels only. Never log arguments, prompts, keys,
      // arbitrary headers, user IDs or provider response bodies.
      if(error.termination)this.providerLog("Responses termination", {
        code:error.code,termination:error.termination,http_status:responseStatus,
        elapsed_ms:Date.now()-startedAt,...trace.snapshot(),
      });
      if (error instanceof OpenAI.AuthenticationError) throw error;

      if (
        error instanceof OpenAI.RateLimitError ||
        error instanceof OpenAI.InternalServerError ||
        error instanceof OpenAI.APIError
      ) {
        throw new RetryError(error.message);
      }

      throw error;
    }
  }

  /**
   * Create a completion based on the received messages.
   *
   * @param messages A list of messages to send to the OpenAI API.
   * @param functions
   * @returns The completion.
   */
  async complete(messages, functions = []) {
    this.providerLog("OpenAI.complete - will process this chat completion.");
    this.resetUsage();

    try {
      const response = await this.client.responses.create({
        model: this.model,
        stream: false,
        store: false,
        parallel_tool_calls: false,
        input: this.#formatToResponsesInput(messages),
        ...(Array.isArray(functions) && functions.length
          ? { tools: this.#formatFunctions(functions) } : {}),
      });
      const assembly = new ResponsesAssembly();
      assembly.push({ type: "response.completed", response });
      const result = assembly.finish();
      const functionCall = singleCall(result);
      if (result.usage) this.recordUsage(result.usage);
      return { termination: result.termination, textResponse: result.textResponse, functionCall,
        cost: this.getCost(), usage: this.getUsage() };
    } catch (error) {
      if (error instanceof OpenAI.AuthenticationError) throw error;
      if (
        error instanceof OpenAI.RateLimitError ||
        error instanceof OpenAI.InternalServerError ||
        error instanceof OpenAI.APIError
      ) {
        throw new RetryError(error.message);
      }

      throw error;
    }
  }

  /**
   * Get the cost of the completion.
   * @returns {number} The cost of the completion (currently returns 0).
   */
  getCost() {
    return 0;
  }
}

module.exports = OpenAIProvider;
