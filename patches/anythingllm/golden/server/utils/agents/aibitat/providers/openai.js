const OpenAI = require("openai");
const Provider = require("./ai-provider.js");
const { RetryError } = require("../error.js");
const { v4 } = require("uuid");
const { decodeResponses, ResponsesAssembly, singleCall, ResponsesTrace, classifyStreamError } = require("./responses-stream.js");
const {
  reasoningParams,
  createWithReasoningSummaryFallback,
} = require("../../../helpers/reasoningEffort");
const {
  formatToResponsesInput,
  formatResponsesTools,
} = require("./helpers/responsesTooled.js");

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
      reasoningEffort = null,
    } = config;

    const client = new OpenAI(options);

    super(client);

    this.providerTag = "openai";
    this.model = model;
    this.reasoningEffort = reasoningEffort;
  }

  /**
   * The reasoning portion of the request body. The effort is validated against
   * the model before the provider is built, so it only needs mapping here.
   * @returns {object}
   */
  get reasoningConfig() {
    return reasoningParams("openai", this.reasoningEffort, this.model);
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
   * Stream a chat completion from the LLM with tool calling via the Responses API.
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
      const response = await createWithReasoningSummaryFallback(
        (body) => this.client.responses.create(body).asResponse(),
        {
          model: this.model,
          input: formatToResponsesInput(messages, this),
          stream: true,
          store: false,
          parallel_tool_calls: false,
          ...this.reasoningConfig,
          ...(Array.isArray(functions) && functions.length
            ? { tools: formatResponsesTools(functions) } : {}),
        }
      );
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
   * Create a completion based on the received messages via the Responses API.
   *
   * @param messages A list of messages to send to the OpenAI API.
   * @param functions
   * @returns The completion.
   */
  async complete(messages, functions = []) {
    this.providerLog("OpenAI.complete - will process this chat completion.");
    this.resetUsage();

    try {
      const response = await createWithReasoningSummaryFallback(
        (body) => this.client.responses.create(body),
        {
          model: this.model,
          stream: false,
          store: false,
          parallel_tool_calls: false,
          ...this.reasoningConfig,
          input: formatToResponsesInput(messages, this),
          ...(Array.isArray(functions) && functions.length
            ? { tools: formatResponsesTools(functions) } : {}),
        }
      );
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
