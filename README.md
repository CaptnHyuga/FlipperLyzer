# Flipper Signal Lab
Drop a Flipper `Protocol: RAW` `.sub` file into the local UI for timing analysis and, for standard OOK presets,
optional analysis with [rtl_433](https://github.com/merbanan/rtl_433).

The parser follows the [Flipper RAW file format](https://github.com/flipperdevices/flipperzero-firmware/blob/dev/documentation/file_formats/SubGhzFileFormats.md)
used by upstream and [Unleashed](https://github.com/DarkFlippers/unleashed-firmware/blob/dev/documentation/file_formats/SubGhzFileFormats.md).
Normalized timings are signed nonzero 32-bit microsecond durations that start positive; the format documents up to 512
durations per `RAW_Data` line. Real captures can diverge from that guidance: leading negative timings are dropped,
adjacent same-sign values are coalesced, oversized lines are accepted, and an odd final pulse receives a synthetic closing
gap. Each adjustment is reported. Zero values, malformed fields, and overflow are still rejected.

Bit patterns are timing heuristics, not protocol identification. rtl_433 conversion is only enabled for standard OOK
presets; FSK and custom presets are not interpreted as OOK. A pulse preceding a long frame gap is treated as a delimiter
and omitted from the estimated bits. Unstructured noise is not presented as a bitstream. The rtl_433 panel shows candidate
matches only; verify checksums such as `mic` and whether decoded values are physically plausible. A normalized `.sub` is an
estimate, not a guaranteed equivalent or replayable copy. Test only with devices and frequencies you are authorized to use.

## Use
    git clone <your-repo-url> && cd flipper-signal-lab
    python3 flipper_signal_lab.py

First run: downloads and compiles rtl_433 (Linux; needs git, cmake, a C compiler) or installs it via Homebrew (macOS).
Later runs: pull updates, rebuild only if rtl_433 changed, then open http://127.0.0.1:8765.
If port 8765 is occupied, the launcher selects an available localhost port and opens that URL.
Windows: the official prebuilt rtl_433 release is downloaded into `tools/bin/` automatically and updated when a newer release appears. The download is checked against GitHub's release-asset SHA-256 digest before extraction. A copy you extracted there yourself is used as is. If it won't start, install the Microsoft Visual C++ Redistributable.

Run the local checks from this directory with `python -m unittest discover -s tests`.

The server only listens on localhost and rejects non-local Host headers and cross-origin API requests; signal files are processed locally. Startup may contact GitHub to check for app and rtl_433 updates. Only use on devices you own or may test.
