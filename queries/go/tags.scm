(function_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params) @definition.function

(method_declaration
  name: (field_identifier) @name
  parameters: (parameter_list) @params) @definition.method

(type_declaration
  (type_spec
    name: (type_identifier) @name)) @definition.type

(import_declaration) @reference.import
