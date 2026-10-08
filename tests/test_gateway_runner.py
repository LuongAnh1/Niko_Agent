import unittest

from niko.gateway import GatewayRunner


class FakeChatGraph:
    def __init__(self) -> None:
        self.calls = []

    def handle_message(self, prompt, gateway_message, deliver_reply, notify_working=None):
        self.calls.append((prompt, gateway_message, deliver_reply, notify_working))
        deliver_reply("ok")
        if notify_working is not None:
            notify_working()
        return "fake_route"


class FakeApp:
    def __init__(self) -> None:
        self.calls = []

    def handle_message(self, prompt, gateway_message, deliver_reply, notify_working=None):
        self.calls.append((prompt, gateway_message, deliver_reply, notify_working))
        deliver_reply("app ok")
        if notify_working is not None:
            notify_working()
        return "app_route"


class GatewayRunnerTests(unittest.TestCase):
    def test_runner_delegates_to_app_without_changing_callbacks(self):
        app = FakeApp()
        runner = GatewayRunner(app=app)
        delivered = []
        notified = []
        message = object()
        deliver = delivered.append
        notify = lambda: notified.append("typing")

        route = runner.handle_message("hello", message, deliver, notify)

        self.assertEqual(route, "app_route")
        self.assertEqual(delivered, ["app ok"])
        self.assertEqual(notified, ["typing"])
        self.assertEqual(len(app.calls), 1)
        prompt, recorded_message, recorded_deliver, recorded_notify = app.calls[0]
        self.assertEqual(prompt, "hello")
        self.assertIs(recorded_message, message)
        self.assertIs(recorded_deliver, deliver)
        self.assertIs(recorded_notify, notify)

    def test_runner_delegates_to_chat_graph_without_changing_callbacks(self):
        graph = FakeChatGraph()
        runner = GatewayRunner(chat_graph=graph)
        delivered = []
        notified = []
        message = object()
        deliver = delivered.append
        notify = lambda: notified.append("typing")

        route = runner.handle_message("hello", message, deliver, notify)

        self.assertEqual(route, "fake_route")
        self.assertEqual(delivered, ["ok"])
        self.assertEqual(notified, ["typing"])
        self.assertEqual(len(graph.calls), 1)
        prompt, recorded_message, recorded_deliver, recorded_notify = graph.calls[0]
        self.assertEqual(prompt, "hello")
        self.assertIs(recorded_message, message)
        self.assertIs(recorded_deliver, deliver)
        self.assertIs(recorded_notify, notify)

    def test_runner_rejects_double_injection(self):
        with self.assertRaises(ValueError):
            GatewayRunner(app=FakeApp(), chat_graph=FakeChatGraph())


if __name__ == "__main__":
    unittest.main()
