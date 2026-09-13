import unittest

from bots.agent_router import (
    ROUTE_BUSY_REPLY,
    ROUTE_DEEP_AGENT,
    ROUTE_FAST_AGENT,
    ROUTE_LOCAL_REPLY,
    decide_agent_route,
    should_use_deep_agent,
)


class AgentRouterTests(unittest.TestCase):
    def test_active_deep_job_returns_busy_reply(self):
        route = decide_agent_route('anh xem giúp em tiếp', deep_job_active=True)

        self.assertEqual(route.kind, ROUTE_BUSY_REPLY)

    def test_short_greeting_returns_local_reply(self):
        route = decide_agent_route('alo Niko ơi')

        self.assertEqual(route.kind, ROUTE_LOCAL_REPLY)
        self.assertIn('anh', route.reply)

    def test_deep_keywords_route_to_deep_agent(self):
        route = decide_agent_route('thiết kế lại gateway Telegram giúp anh', fast_agent_available=True)

        self.assertEqual(route.kind, ROUTE_DEEP_AGENT)

    def test_simple_unknown_uses_fast_agent_when_available(self):
        route = decide_agent_route('thời tiết đẹp không', fast_agent_available=True)

        self.assertEqual(route.kind, ROUTE_FAST_AGENT)

    def test_simple_unknown_without_fast_agent_falls_back_to_deep(self):
        route = decide_agent_route('thời tiết đẹp không', fast_agent_available=False)

        self.assertEqual(route.kind, ROUTE_DEEP_AGENT)

    def test_long_prompt_uses_deep_agent(self):
        self.assertTrue(should_use_deep_agent('a' * 220))


if __name__ == '__main__':
    unittest.main()
