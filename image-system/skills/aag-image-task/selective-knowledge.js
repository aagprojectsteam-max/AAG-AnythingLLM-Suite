"use strict";

const visualAtlas = require("./visual-atlas");

// Knowledge modules remain independent and bounded. A future cultural module
// can join this list without merging its taxonomy or data with the Visual Atlas.
const MODULES = Object.freeze([visualAtlas]);

function applyToTask(task, context = {}) {
  for (const module of MODULES) module.applyToTask(task, context);
  return task;
}

module.exports = { MODULES, applyToTask };
