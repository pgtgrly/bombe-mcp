(class_declaration
  name: (identifier) @name) @definition.class

(interface_declaration
  name: (identifier) @name) @definition.interface

(method_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.method

(import_declaration) @reference.import
