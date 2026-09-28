# 001: WMP ActiveX video host

## Question

Can the WMP9 ActiveX control display Wine DirectShow video when the full WMP9 application advances playback but leaves its renderer black?

## Hypothesis and acceptance criterion

Given a short MJPEG-and-PCM AVI that WMP identifies as video, playing it in a visible `WMPlayer.OCX.7` HTA host should show the FFmpeg test pattern rather than a black panel or an audio visualization.

The spike is successful only when real frame pixels are visible. Playback state, duration, audio, or an advancing timeline are supporting diagnostics, not proof of video output.

## Fixture selection

`player.hta` reads the media path from the process environment variable `WMP9_SPIKE_MEDIA`. The value should be a Windows path visible inside the test Wine prefix. If the variable is absent, the HTA derives this default from Wine's `HOME` value:

```text
Z:<HOME>\.cache\wmp9-video-visual-test\mjpeg-pcm.avi
```

This avoids embedding a developer username or machine-specific home path in the spike.

## Safety constraints

- Run only in an isolated test Wine prefix or a disposable copy of the target prefix.
- Use a non-DRM local fixture generated for this test and only repository-owned assets.
- The HTA makes no network requests and does not register codecs or modify the prefix.
- Close the HTA normally after capturing the result; do not kill unrelated Wine processes.

## Verdict: PENDING
