from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bombe.plugins import PluginManager


class PluginManagerTests(unittest.TestCase):
    def test_plugin_manager_loads_path_plugin_and_runs_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plugin_file = repo_root / "demo_plugin.py"
            plugin_file.write_text(
                "\n".join(
                    [
                        "class DemoPlugin:",
                        "    def before_query(self, tool_name, payload):",
                        "        out = dict(payload)",
                        "        out['plugin_marker'] = tool_name",
                        "        return out",
                        "",
                        "    def before_index(self, mode, payload):",
                        "        out = dict(payload)",
                        "        out['workers'] = 1",
                        "        return out",
                        "",
                        "def build_plugin():",
                        "    return DemoPlugin()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            config_path = repo_root / ".bombe" / "plugins.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "plugins": [
                            {
                                "path": plugin_file.as_posix(),
                                "enabled": True,
                                "timeout_ms": 1000,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            manager = PluginManager.from_repo(repo_root)
            query_payload = manager.before_query("search_symbols", {"query": "auth"})
            self.assertEqual(query_payload["plugin_marker"], "search_symbols")
            index_payload = manager.before_index("full", {"workers": 4})
            self.assertEqual(int(index_payload["workers"]), 1)
            manager.after_query("search_symbols", query_payload, {"symbols": []}, error=None)
            manager.after_index("full", {"files_indexed": 1}, error=None)
            stats = manager.stats()
            self.assertEqual(int(stats["plugins_loaded"]), 1)
            self.assertGreaterEqual(int(stats["hook_calls"]), 2)


if __name__ == "__main__":
    unittest.main()
