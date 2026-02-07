(function_definition
  name: (identifier) @name
  parameters: (parameters) @params) @definition.function

(class_definition
  name: (identifier) @name) @definition.class

(import_statement) @reference.import
(import_from_statement) @reference.import
