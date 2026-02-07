(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.function

(class_declaration
  name: (type_identifier) @name) @definition.class

(interface_declaration
  name: (type_identifier) @name) @definition.interface

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params) @definition.method

(import_statement) @reference.import
