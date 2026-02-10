from __future__ import annotations

import json
import unittest
from pathlib import Path


class IntegrationAssetTests(unittest.TestCase):
    def test_deploy_assets_exist_and_are_non_empty(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = root / "deploy" / "docker" / "Dockerfile"
        compose_file = root / "deploy" / "docker" / "docker-compose.yml"
        k8s_deployment = root / "deploy" / "k8s" / "deployment.yaml"
        k8s_service = root / "deploy" / "k8s" / "service.yaml"
        vscode_package = root / "integrations" / "vscode" / "package.json"
        vscode_extension = root / "integrations" / "vscode" / "extension.js"
        vscode_readme = root / "integrations" / "vscode" / "README.md"

        for path in (
            dockerfile,
            compose_file,
            k8s_deployment,
            k8s_service,
            vscode_package,
            vscode_extension,
            vscode_readme,
        ):
            self.assertTrue(path.exists(), f"missing asset: {path}")
            self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 0)

        package_payload = json.loads(vscode_package.read_text(encoding="utf-8"))
        self.assertEqual(str(package_payload["name"]), "bombe-vscode")
        self.assertIn("bombe.status", str(package_payload["activationEvents"][0]))

        deployment_text = k8s_deployment.read_text(encoding="utf-8")
        self.assertIn("kind: Deployment", deployment_text)
        self.assertIn("name: bombe", deployment_text)


if __name__ == "__main__":
    unittest.main()
