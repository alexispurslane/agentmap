"""Behavior of the caller finder: which thread made an MCP call without a thread header."""

import unittest

from agentmap.caller import CallerError, CallerFinder


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Lookup:
    """Returns the queued answers in order, then the last one forever."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, tool, arguments):
        self.calls.append((tool, arguments))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def finder(lookup, time=None):
    time = time or FakeTime()
    return CallerFinder(lookup, timeout=2.0, interval=0.05, clock=time.clock, sleep=time.sleep), time


class CallerFinderTest(unittest.TestCase):
    def test_a_recorded_call_names_its_thread_at_once(self):
        lookup = Lookup([("i1", "t1")])
        find, time = finder(lookup)
        self.assertEqual(find("set_goal", {"goal": "G"}), "t1")
        self.assertEqual(lookup.calls, [("set_goal", {"goal": "G"})])
        self.assertEqual(time.t, 0.0)

    def test_the_finder_waits_until_t3_code_records_the_call(self):
        find, time = finder(Lookup([], [], [("i1", "t1")]))
        self.assertEqual(find("get_plan", {}), "t1")
        self.assertAlmostEqual(time.t, 0.10)

    def test_no_record_within_the_timeout_is_an_error_that_names_the_header(self):
        find, time = finder(Lookup([]))
        with self.assertRaises(CallerError) as caught:
            find("get_plan", {})
        self.assertGreaterEqual(time.t, 2.0)
        self.assertLess(time.t, 2.1)
        self.assertIn("X-T3-Thread", str(caught.exception))

    def test_one_record_serves_one_request(self):
        find, _ = finder(Lookup([("i1", "t1")]))
        self.assertEqual(find("get_plan", {}), "t1")
        with self.assertRaises(CallerError):
            find("get_plan", {})

    def test_a_newer_record_of_the_same_thread_serves_a_repeat_call(self):
        find, _ = finder(Lookup([("i1", "t1"), ("i2", "t1")]))
        self.assertEqual(find("get_plan", {}), "t1")
        self.assertEqual(find("get_plan", {}), "t1")
        with self.assertRaises(CallerError):
            find("get_plan", {})

    def test_two_threads_with_the_same_call_at_once_get_an_error_that_asks_to_call_again(self):
        find, time = finder(Lookup([("i1", "t1"), ("i2", "t2")]))
        with self.assertRaises(CallerError) as caught:
            find("get_plan", {})
        self.assertIn("again", str(caught.exception))
        self.assertEqual(time.t, 0.0)

    def test_after_a_claim_the_other_threads_record_is_unambiguous(self):
        lookup = Lookup([("i2", "t2")], [("i1", "t1"), ("i2", "t2")])
        find, _ = finder(lookup)
        self.assertEqual(find("get_plan", {}), "t2")
        self.assertEqual(find("get_plan", {}), "t1")

    def test_claims_are_forgotten_after_ten_minutes(self):
        find, time = finder(Lookup([("i1", "t1")]))
        find("get_plan", {})
        time.t = 300.0
        with self.assertRaises(CallerError):
            find("get_plan", {})
        time.t = 600.0
        self.assertEqual(find("get_plan", {}), "t1")
        self.assertEqual(time.t, 600.0)


    def test_a_claim_lasts_ten_minutes_from_the_moment_it_was_made(self):
        find, time = finder(Lookup([("i1", "t1")]))
        time.t = 500.0
        find("get_plan", {})
        time.t = 800.0
        with self.assertRaises(CallerError):
            find("get_plan", {})

    def test_the_finder_looks_one_last_time_at_the_deadline_and_then_stops(self):
        lookup = Lookup([])
        time = FakeTime()
        find = CallerFinder(lookup, timeout=2.0, interval=0.5, clock=time.clock, sleep=time.sleep)
        with self.assertRaises(CallerError):
            find("get_plan", {})
        self.assertEqual(len(lookup.calls), 5)
        self.assertEqual(time.t, 2.0)


if __name__ == "__main__":
    unittest.main()
