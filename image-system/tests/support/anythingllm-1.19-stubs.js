"use strict";
// Modules AnythingLLM 1.19.0 added around the provider files. The Suite tests
// load those files in a vm with a closed dependency list, so the new modules
// must be supplied here. formatToResponsesInput and formatResponsesTools are
// copied from v1.19.0 (MIT) because AAG's request history depends on their
// exact output. Extra keys are harmless for sources that do not require them.

function temperatureParam(temperature) {
  if (typeof temperature !== "number" || !Number.isFinite(temperature)) return {};
  return { temperature };
}

function formatToResponsesInput(messages = [], provider = null) {
  const formattedMessages = [];
  messages.forEach((message) => {
    if (message.role === "function") {
      if (!message.hasOwnProperty("originalFunctionCall")) {
        provider?.providerLog?.("[formatToResponsesInput]: missing originalFunctionCall", { message: JSON.stringify(message, null, 2) });
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
    const content = [
      { type: message.role === "assistant" ? "output_text" : "input_text", text: message.content },
    ];
    if (message.attachments && message.attachments.length > 0) {
      for (const attachment of message.attachments) {
        content.push({ type: "input_image", image_url: attachment.contentString });
      }
    }
    formattedMessages.push({ role: message.role, content });
  });
  return formattedMessages;
}

function formatResponsesTools(functions = []) {
  return functions.map((func) => ({
    type: "function",
    name: func.name,
    description: func.description,
    parameters: func.parameters,
    strict: false,
  }));
}

const reasoningEffort = {
  reasoningParams: () => ({}),
  modelsDevReasoningCapabilities: () => null,
  createWithReasoningSummaryFallback: async (create, body) => create(body),
};

// Agent provider files, loaded from aibitat/providers/.
function agentProvider(existing = {}) {
  return {
    "../../../helpers/reasoningEffort": reasoningEffort,
    "./helpers/responsesTooled.js": { formatToResponsesInput, formatResponsesTools },
    "./helpers/tooled.js": { ...(existing["./helpers/tooled.js"] || {}), temperatureParam },
  };
}

// aibitat/index.js.
const aibitat = { "../../helpers": { resolveTemperature: (_provider, _model, value) => value } };

// AiProviders/openAi/index.js.
const ordinaryOpenAi = {
  "../../helpers/reasoningEffort": reasoningEffort,
  "../../agents/aibitat/providers/helpers/tooled": { temperatureParam },
};

module.exports = { agentProvider, aibitat, ordinaryOpenAi };
