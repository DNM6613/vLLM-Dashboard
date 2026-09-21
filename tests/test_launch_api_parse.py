import unittest

from backend.config import model_launch_config as mlc
from backend.schemas.model import ModelStatus


class TestParseLaunchApi(unittest.TestCase):
    def test_command_flags(self):
        self.assertEqual(
            mlc.parse_launch_api(
                "vllm serve /opt/models/Qwen --port 8001 --api-key sk-test", ""),
            (8001, "sk-test"),
        )

    def test_command_equals_form(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --port=8002 --api-key=sk-eq", ""),
            (8002, "sk-eq"),
        )

    def test_api_keys_comma_list_takes_first(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --api-keys sk-a,sk-b", ""),
            (None, "sk-a"),
        )

    def test_env_only(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m", "PORT=9000\nAPI_KEY=k1"),
            (9000, "k1"),
        )

    def test_command_wins_over_env(self):
        self.assertEqual(
            mlc.parse_launch_api(
                "vllm serve /m --port 8001 --api-key c1",
                "PORT=9999\nAPI_KEY=e1"),
            (8001, "c1"),
        )

    def test_env_fills_missing_command_parts(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --port 8001", "API_KEY=e1"),
            (8001, "e1"),
        )

    def test_export_prefix_env(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m", "export API_KEY=k2"),
            (None, "k2"),
        )

    def test_nothing_specified(self):
        self.assertEqual(mlc.parse_launch_api("vllm serve /m", ""), (None, None))

    def test_invalid_port_ignored(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --port abc", ""),
            (None, None),
        )

    def test_out_of_range_port_ignored(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --port 99999", ""),
            (None, None),
        )

    def test_source_activate_prefix(self):
        self.assertEqual(
            mlc.parse_launch_api(
                "source ~/.vllm/bin/activate && vllm serve /m --port 8003", ""),
            (8003, None),
        )

    def test_invalid_env_line_does_not_break_command_port(self):
        self.assertEqual(
            mlc.parse_launch_api("vllm serve /m --port 8000", "bar FOO=qux"),
            (8000, None),
        )

    def test_empty_inputs(self):
        self.assertEqual(mlc.parse_launch_api("", ""), (None, None))


class _FakeModel:
    def __init__(self, id, status):
        self.id = id
        self.status = status

class _FakeState:
    def __init__(self, models, current_id=None):
        self._models = models
        self.current_model_id = current_id

    def get_all_models(self):
        return list(self._models)

class _FakeLaunchConfigManager:
    def __init__(self, configs):
        self._configs = configs

    def load_config(self, model_id):
        return self._configs.get(model_id)

class TestResolveLaunchApi(unittest.TestCase):
    def setUp(self):
        self._state = mlc.state_machine
        self._lcm = mlc.launch_config_manager
        self._configs = {}
        mlc.launch_config_manager = _FakeLaunchConfigManager(self._configs)
        mlc.state_machine = _FakeState([], None)

    def tearDown(self):
        mlc.state_machine = self._state
        mlc.launch_config_manager = self._lcm

    def test_no_candidates_returns_none(self):
        self.assertEqual(mlc.resolve_launch_api(), (None, None))

    def test_stopped_models_ignored(self):
        mlc.state_machine = _FakeState(
            [_FakeModel("a", ModelStatus.STOPPED)], current_id=None)
        self._configs["a"] = {
            "start_command": "vllm serve /m --port 8400", "env_vars": ""}
        self.assertEqual(mlc.resolve_launch_api(), (None, None))

    def test_running_model_config_used(self):
        mlc.state_machine = _FakeState(
            [_FakeModel("m1", ModelStatus.RUNNING)], current_id="m1")
        self._configs["m1"] = {
            "start_command": "vllm serve /m --port 8100 --api-key k1",
            "env_vars": ""}
        self.assertEqual(mlc.resolve_launch_api(), (8100, "k1"))

    def test_current_without_config_falls_back_to_running(self):
        mlc.state_machine = _FakeState(
            [_FakeModel("a", ModelStatus.RUNNING),
             _FakeModel("b", ModelStatus.RUNNING)],
            current_id="a")
        self._configs["b"] = {
            "start_command": "vllm serve /m --port 8200", "env_vars": ""}
        self.assertEqual(mlc.resolve_launch_api(), (8200, None))

    def test_loading_preferred_over_non_current_running(self):
        mlc.state_machine = _FakeState(
            [_FakeModel("a", ModelStatus.RUNNING),
             _FakeModel("b", ModelStatus.RUNNING),
             _FakeModel("c", ModelStatus.LOADING)],
            current_id="a")
        self._configs["a"] = {"start_command": "vllm serve /m", "env_vars": ""}
        self._configs["b"] = {
            "start_command": "vllm serve /m --port 8300", "env_vars": ""}
        self._configs["c"] = {
            "start_command": "vllm serve /m --port 9300", "env_vars": ""}
        # a (current) yields nothing; among the rest the LOADING model c is
        # preferred over the RUNNING model b.
        self.assertEqual(mlc.resolve_launch_api(), (9300, None))

    def test_stale_current_id_does_not_shadow_running_model(self):
        # current id points to a stopped model; the RUNNING model's config
        # must win.
        mlc.state_machine = _FakeState(
            [_FakeModel("a", ModelStatus.STOPPED),
             _FakeModel("b", ModelStatus.RUNNING)],
            current_id="a")
        self._configs["a"] = {
            "start_command": "vllm serve /m --port 8500", "env_vars": ""}
        self._configs["b"] = {
            "start_command": "vllm serve /m --port 8600 --api-key kb",
            "env_vars": ""}
        self.assertEqual(mlc.resolve_launch_api(), (8600, "kb"))


if __name__ == "__main__":
    unittest.main()
