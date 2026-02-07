from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.parser import parse_file
from bombe.indexer.symbols import extract_symbols


class SymbolExtractionTests(unittest.TestCase):
    def test_extract_python_symbols_and_imports(self) -> None:
        source = (
            "import os\n"
            "from app.auth import login\n"
            "\n"
            "class Service:\n"
            "    def run(self, user):\n"
            "        return user\n"
            "\n"
            "async def handler(name: str):\n"
            "    return name\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "service.py"
            path.write_text(source, encoding="utf-8")

            parsed = parse_file(path, "python")
            symbols, imports = extract_symbols(parsed)

            names = sorted((symbol.name, symbol.kind) for symbol in symbols)
            self.assertIn(("Service", "class"), names)
            self.assertIn(("run", "method"), names)
            self.assertIn(("handler", "function"), names)

            handler = next(symbol for symbol in symbols if symbol.name == "handler")
            self.assertTrue(handler.is_async)
            self.assertGreaterEqual(handler.end_line, handler.start_line)

            import_modules = sorted(import_record.module_name for import_record in imports)
            self.assertEqual(import_modules, ["app.auth", "os"])

    def test_extract_java_symbols_and_imports(self) -> None:
        source = (
            "package com.example.auth;\n"
            "import java.util.List;\n"
            "\n"
            "public class AuthService {\n"
            "    public boolean login(String user, String pass) {\n"
            "        return true;\n"
            "    }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "AuthService.java"
            path.write_text(source, encoding="utf-8")
            parsed = parse_file(path, "java")
            symbols, imports = extract_symbols(parsed)

            names = sorted((symbol.name, symbol.kind) for symbol in symbols)
            self.assertIn(("AuthService", "class"), names)
            self.assertIn(("login", "method"), names)
            self.assertEqual(imports[0].module_name, "java.util.List")
            login = next(symbol for symbol in symbols if symbol.name == "login")
            self.assertEqual(login.parameters[0].name, "user")
            self.assertEqual(login.parameters[0].type, "String")

    def test_extract_typescript_symbols_and_imports(self) -> None:
        source = (
            "import { User } from './models';\n"
            "export interface AuthPort {\n"
            "  login(name: string): Promise<boolean>;\n"
            "}\n"
            "export class AuthService {\n"
            "  async login(name: string): Promise<boolean> {\n"
            "    return true;\n"
            "  }\n"
            "}\n"
            "export const makeToken = (name: string) => name + '-token';\n"
            "export function logout(userId: string) { return true; }\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "auth.ts"
            path.write_text(source, encoding="utf-8")
            parsed = parse_file(path, "typescript")
            symbols, imports = extract_symbols(parsed)

            names = sorted((symbol.name, symbol.kind) for symbol in symbols)
            self.assertIn(("AuthPort", "interface"), names)
            self.assertIn(("AuthService", "class"), names)
            self.assertIn(("login", "method"), names)
            self.assertIn(("makeToken", "function"), names)
            self.assertIn(("logout", "function"), names)
            self.assertEqual(imports[0].module_name, "./models")
            logout = next(symbol for symbol in symbols if symbol.name == "logout")
            self.assertEqual(logout.parameters[0].name, "userId")
            self.assertEqual(logout.parameters[0].type, "string")

    def test_extract_go_symbols_and_imports(self) -> None:
        source = (
            "package auth\n"
            "import (\n"
            "  \"context\"\n"
            ")\n"
            "type Service struct {}\n"
            "type Reader interface {}\n"
            "func Login(name string) bool {\n"
            "  return true\n"
            "}\n"
            "func (s *Service) Verify(token string) bool {\n"
            "  return true\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "service.go"
            path.write_text(source, encoding="utf-8")
            parsed = parse_file(path, "go")
            symbols, imports = extract_symbols(parsed)

            names = sorted((symbol.name, symbol.kind) for symbol in symbols)
            self.assertIn(("Service", "class"), names)
            self.assertIn(("Reader", "interface"), names)
            self.assertIn(("Login", "function"), names)
            self.assertIn(("Verify", "method"), names)
            self.assertEqual(imports[0].module_name, "context")
            login = next(symbol for symbol in symbols if symbol.name == "Login")
            self.assertEqual(login.parameters[0].name, "name")
            self.assertEqual(login.parameters[0].type, "string")


if __name__ == "__main__":
    unittest.main()
