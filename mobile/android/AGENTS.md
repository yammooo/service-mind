# Android companion scope

This subtree is the separately authorized ServiceMind Android worker companion. It may integrate Meta Wearables Device Access Toolkit 1.0.1 and MockDeviceKit despite the prototype-wide Meta exclusion above; that exception applies only here and does not change the backend, camera service, or UNO Q deployment.

Keep the prototype small: native Android UI, existing HTTP API, one-second state polling, editable local backend URL, one selected worker, press-to-talk/final transcript handling, and a clear MockDevice mode. No cloud speech, LLM, camera access, login, new backend, or new state store. Test API mutations only against a temporary SQLite database and local test server; never reset or mutate the user's demo server during validation.

DAT speech is experimental. A MockDeviceKit injected transcript proves the app's SDK callback and command flow, not acoustic recognition or real glasses behavior. Do not claim hardware validation. Keep SDK analytics and crash reporting opted out for this prototype.
