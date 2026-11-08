This beta merges upstream OpenX6X00 v0.3.9 (733c425e40b3bbd41e1bb3eac098edfc1a8f2cb4) with the USB FreeDV half-duplex feature. It retains live microphone-gain adjustment, headset volume through the normal selected Volume control, panel PTT handling, warm codec operation, and cleanup when leaving FreeDV.

The verified baseline image and matching Buildroot 2023.02.11 SDK are retained; that build configuration selects Linux 6.1.82. Image validation preserves the existing boot-region bytes and checks runtime-library compatibility. The application runtime includes the newer pinned FT8 library and the reviewed Pat/XARA/Mercury/ARDOP programs and scripts; this is not a kernel upgrade. Baseband firmware is unchanged.

Settings migration preserves FreeDV preferences and existing bookmarks, including intentional deletions. An older version-14 database cannot always reveal whether its action-126 binding meant FreeDV or upstream Propagation; this beta preserves that binding as FreeDV. Review that shortcut if you used Propagation on an upstream version-14 image.

Download the image ZIP and matching helper kit from this release; START-HERE.md provides installation and operation instructions. Older working beta releases remain available. Use a USB audio device connected to the HOST port; built-in/hand microphone and radio speaker audio remain unsupported for FreeDV, and TX uses the radio's panel PTT rather than hand-mic PTT.

Software qualification passed for five build variants, 75 host tests, eight upstream suites with 1,320 assertions, and all 104 half-duplex cases repeated twice (208 executions). Independent scheduling/resource checks and exact-source Windows, Linux and USB-controller GitHub checks also passed. Detailed evidence is recorded in software-verification.json. These are software and emulation results, not a new hardware qualification. Live peer RADE reception remains field validation.

Development and releases remain in bho18/OpenX6X00. Nothing is pushed to upstream. The integration guide in the source documents subsequent upstream merges and reproducible build/release steps.
