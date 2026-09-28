"""Create and release a private PulseAudio startup gate for a new WMP process.

The launcher creates a null sink *before* Wine starts and passes its name through
``PULSE_SINK``.  This confines the new WMP streams without changing the global
default sink, muting unrelated applications, or stopping the player process.
Once the overlay proves that WMP is held at zero and mpv has a visible aligned
frame, only sink inputs still owned by this private sink are moved to the saved
physical target.  The launcher owns the module lifetime and must call
:meth:`AudioGate.close` during cleanup.
"""
import json
import subprocess
import uuid


def pactl(*args):
    """Run one bounded pactl command and return stdout."""
    return subprocess.run(['pactl',*args],check=True,text=True,capture_output=True,timeout=3).stdout


class AudioGate:
    """Own one temporary null sink and the original default-sink destination."""

    def __init__(self, run=pactl, name=None):
        """Create an unopened gate using an injectable pactl runner for tests."""
        self.run=run
        self.name=name or ('wmp9_gate_'+uuid.uuid4().hex)
        self.module=None
        self.target=None
        self.index=None

    def open(self):
        """Create the silent sink and resolve its numeric index for ownership checks."""
        self.target=self.run('get-default-sink').strip()
        self.module=self.run('load-module','module-null-sink',f'sink_name={self.name}','sink_properties=device.description=WMP9-startup-gate').strip()
        sinks=json.loads(self.run('-f','json','list','sinks'))
        self.index=next(s['index'] for s in sinks if s['name']==self.name)

    def environment(self):
        """Return variables inherited by WMP and the overlay release service."""
        return {'PULSE_SINK':self.name,'WMP9_GATE_SINK':str(self.index),'WMP9_GATE_TARGET':self.target}

    def release(self):
        """Move this gate's streams to the captured target without unloading it."""
        release_gate(self.index,self.target,self.run)

    def close(self):
        """Unload the owned module; safe to call again after successful cleanup."""
        if self.module is not None:
            self.run('unload-module',self.module)
            self.module=None


def release_gate(index,target,run=pactl):
    """Move only streams currently attached to ``index`` and verify the move.

    Failing closed when no owned stream exists avoids turning a missing or late
    gate into an ungated startup.  Re-reading sink inputs is required because a
    successful ``pactl`` exit alone does not prove that routing changed.
    """
    inputs=json.loads(run('-f','json','list','sink-inputs'))
    owned=[s for s in inputs if str(s['sink'])==str(index)]
    if not owned:
        raise RuntimeError('WMP startup gate has no stream; refusing ungated release')
    for stream in owned:
        run('move-sink-input',str(stream['index']),target)
    remaining=json.loads(run('-f','json','list','sink-inputs'))
    if any(str(s['sink'])==str(index) for s in remaining):
        raise RuntimeError('WMP audio gate release did not take effect')
