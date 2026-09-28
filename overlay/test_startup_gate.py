"""Unit regression for the private PulseAudio startup gate.

The test supplies a mock ``pactl`` runner, so it neither creates sinks nor moves
real desktop audio streams.
"""

import importlib.util
import unittest
from unittest.mock import Mock


class StartupGateTests(unittest.TestCase):
    """Protect private-sink routing, read-back verification, and cleanup order."""

    def test_gate_routes_only_private_streams_and_reads_back(self):
        """Release must move only gated inputs, verify them, then unload the sink."""
        spec = importlib.util.find_spec('startup_gate')
        self.assertIsNotNone(
            spec,
            'startup audio needs a pre-created silent destination, not late mute',
        )
        from startup_gate import AudioGate

        run = Mock(side_effect=[
            'speaker\n',
            '123\n',
            '[{"name":"gate_test","index":900}]',
            '[{"index":1,"sink":900},{"index":2,"sink":3}]',
            '',
            '[{"index":1,"sink":3},{"index":2,"sink":3}]',
            '',
        ])
        gate = AudioGate(run=run, name='gate_test')
        gate.open()
        self.assertEqual(gate.environment()['PULSE_SINK'], 'gate_test')
        gate.release()
        gate.close()
        calls = [call.args for call in run.call_args_list]
        self.assertIn(('move-sink-input', '1', 'speaker'), calls)
        self.assertNotIn(('move-sink-input', '2', 'speaker'), calls)
        self.assertEqual(calls[-1], ('unload-module', '123'))


if __name__ == '__main__':
    unittest.main()
