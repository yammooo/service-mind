# ServiceMind Android worker companion

This is a small native Android client for the existing ServiceMind HTTP API. It polls shared state once per second and provides worker A/B selection, availability, task actions, dessert/water, and not-ready controls. API writes are sent only to the configured backend; the app has no separate state store.

## Build and install

Requirements: Android SDK platform 36, build tools 36.0.0, and JDK 17. The Gradle wrapper uses Gradle 8.13, Android Gradle Plugin 8.13.2, and Kotlin 2.2.10.

```bash
cd mobile/android
./gradlew testDebugUnitTest assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The debug APK is `app/build/outputs/apk/debug/app-debug.apk`. For an emulator, the default backend URL is `http://10.0.2.2:7001`, which reaches port 7001 on the development host. On a phone, enter the host's trusted demo-Wi-Fi address in Backend URL and tap Connect.

## Run against an isolated backend

From the repository root, start the existing server against a disposable database and mock vision provider. This is for local testing; it does not use or reset the live demo database.

```bash
SERVICE_MIND_MODE=legacy \
SERVICE_MIND_DB=/tmp/service-mind-android-demo.sqlite3 \
VISION_PROVIDER=mock \
uvicorn backend.main:app --host 127.0.0.1 --port 7001 --timeout-graceful-shutdown 5
```

The prototype API has no authentication. Keep it bound to loopback or a trusted demo Wi-Fi network; do not expose it publicly. The Android app allows HTTP for this local prototype and validates that the entered URL is only an HTTP(S) origin.

## Validation status

The source compiles, five JVM unit tests pass, and the backend API flow was exercised against a loopback server with a disposable SQLite database. This host's Android emulator exits with SIGSEGV before Android finishes booting, so the DAT MockDevice session and injected speech flow have not been runtime-tested here. No phone or physical glasses were tested.

## Mock glasses flow and limits

The app uses Meta Wearables DAT 1.0.1 `mwdat-core`, `mwdat-mockdevice`, and experimental `mwdat-speech`. MockDeviceKit pairs a simulated Ray-Ban Meta device and starts a DAT session without requiring physical glasses. Once a new offered task is announced by Android TTS, the app binds that task to the selected worker and opens a six-second DAT speech window. A final transcript is parsed locally and can trigger an API action; unknown text, interim transcripts, stale state, timeouts, worker changes, and backend changes do not mutate state. The app speaks an action confirmation only after the backend confirms it.

For deterministic SDK-flow testing, while a listening window is open, use the mock transcript field and Inject mock transcript. The text is injected through the SDK's simulated transcription service and collected through the same SDK transcription flow used by the controller. This verifies the app's mock-session, transcript, command, and API wiring. It does **not** test acoustic speech recognition, real glasses audio, Bluetooth pairing, or behavior on physical Ray-Ban Meta glasses. The six-second window is a simulated interaction window; physical glasses support remains step 5 and is unverified.

The app does not request phone microphone or camera access and contains no cloud speech, LLM, or login integration. DAT analytics and crash reporting are opted out in the manifest. The display explicitly identifies MockDevice mode.
