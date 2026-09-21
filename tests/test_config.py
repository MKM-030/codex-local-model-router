import importlib.util, json, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "configure_bridge", ROOT / "scripts/configure_tool_bridge.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ConfigTests(unittest.TestCase):
    def test_preserves_existing(self):
        before = 'model="example"\n\n[features]\nplugins=true\n\n[mcp_servers.test]\ncommand="python"\n'
        after = helper.set_value(before, "features", "standalone_web_search", "true")
        self.assertIn("plugins=true", after)
        self.assertIn('command="python"', after)
        self.assertEqual(
            helper.tomllib.loads(after)["features"]["standalone_web_search"], True
        )

    def test_reinstall_restore(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            state = Path(d) / "state.json"
            p.write_text('model="example"\n[features]\nplugins=true\n')
            helper.configure(p, state)
            helper.configure(p, state)
            self.assertIsNone(
                json.loads(state.read_text())["previousStandaloneWebSearch"]
            )
            helper.configure(p, state, True)
            self.assertEqual(
                helper.tomllib.loads(p.read_text()),
                {"model": "example", "features": {"plugins": True}},
            )

    def test_restore_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            state = Path(d) / "state.json"
            p.write_text("[features]\nstandalone_web_search=false\n")
            helper.configure(p, state)
            helper.configure(p, state, True)
            self.assertFalse(
                helper.tomllib.loads(p.read_text())["features"]["standalone_web_search"]
            )

    def test_inline_refused(self):
        with self.assertRaises(ValueError):
            helper.set_value(
                "features={plugins=true}\n", "features", "standalone_web_search", "true"
            )

    def test_concurrent_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_bytes(b"changed")
            with self.assertRaises(RuntimeError):
                helper.atomic_write(p, b"new", expected=b"old")
            self.assertEqual(p.read_bytes(), b"changed")


if __name__ == "__main__":
    unittest.main()
