import json
import os
import tempfile
import unittest

from devws.services.config import ConfigError, ConfigStore


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "cfg", "config.json")
        self.root = os.path.join(self.tmp.name, "workspace-root")
        os.makedirs(self.root)

    def store(self):
        return ConfigStore(self.config_path)

    def test_starts_empty(self):
        store = self.store()
        self.assertEqual(store.list_workspaces(), [])
        self.assertIsNone(store.active_workspace())

    def test_add_workspace_persists_and_reloads(self):
        store = self.store()
        ws = store.add_workspace("My Projects", self.root)
        self.assertEqual(ws["name"], "My Projects")
        self.assertEqual(ws["root"], self.root)

        reloaded = self.store()
        self.assertEqual(len(reloaded.list_workspaces()), 1)
        self.assertEqual(reloaded.active_workspace()["id"], ws["id"])

    def test_add_workspace_rejects_missing_dir(self):
        with self.assertRaises(ConfigError):
            self.store().add_workspace("x", os.path.join(self.tmp.name, "nope"))

    def test_add_workspace_rejects_empty_name_and_duplicate_root(self):
        store = self.store()
        with self.assertRaises(ConfigError):
            store.add_workspace("   ", self.root)
        store.add_workspace("a", self.root)
        with self.assertRaises(ConfigError):
            store.add_workspace("b", self.root)

    def test_remove_workspace_updates_active(self):
        store = self.store()
        first = store.add_workspace("one", self.root)
        other_root = os.path.join(self.tmp.name, "other")
        os.makedirs(other_root)
        second = store.add_workspace("two", other_root)
        self.assertEqual(store.active_workspace()["id"], second["id"])

        self.assertTrue(store.remove_workspace(second["id"]))
        self.assertEqual(store.active_workspace()["id"], first["id"])
        self.assertFalse(store.remove_workspace("does-not-exist"))

    def test_set_active_unknown_raises(self):
        with self.assertRaises(ConfigError):
            self.store().set_active("nope")

    def test_project_settings_roundtrip_and_cleanup(self):
        store = self.store()
        ws = store.add_workspace("one", self.root)
        store.set_project_settings(ws["id"], "api", {"dev_command": "npm run dev"})
        self.assertEqual(
            store.get_project_settings(ws["id"], "api"),
            {"dev_command": "npm run dev"},
        )
        # empty value removes the key; empty dict removes the project entry
        store.set_project_settings(ws["id"], "api", {"dev_command": ""})
        self.assertEqual(store.get_project_settings(ws["id"], "api"), {})

    def test_project_settings_rejects_unknown_keys(self):
        store = self.store()
        ws = store.add_workspace("one", self.root)
        with self.assertRaises(ConfigError):
            store.set_project_settings(ws["id"], "api", {"evil": "x"})

    def test_atomic_save_leaves_no_tmp_files(self):
        store = self.store()
        store.add_workspace("one", self.root)
        siblings = os.listdir(os.path.dirname(self.config_path))
        self.assertEqual(siblings, ["config.json"])

    def test_corrupt_config_raises_config_error(self):
        os.makedirs(os.path.dirname(self.config_path))
        with open(self.config_path, "w") as fh:
            fh.write("{ not json")
        with self.assertRaises(ConfigError):
            self.store()

    def test_saved_file_is_valid_json(self):
        store = self.store()
        store.add_workspace("one", self.root)
        store.update_settings({"editor": "code", "theme": "dark"})
        with open(self.config_path) as fh:
            data = json.load(fh)
        self.assertEqual(data["settings"], {"editor": "code", "theme": "dark"})

    def test_global_settings_reject_unknown_keys(self):
        with self.assertRaises(ConfigError):
            self.store().update_settings({"hax": True})


if __name__ == "__main__":
    unittest.main()
