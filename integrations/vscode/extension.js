"use strict";

function activate(context) {
  const disposable = {
    dispose() {}
  };
  context.subscriptions.push(disposable);
}

function deactivate() {}

module.exports = {
  activate,
  deactivate
};
