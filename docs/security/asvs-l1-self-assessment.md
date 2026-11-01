# ALC security self-assessment (OWASP ASVS 5.0.0)

Date: 2026-10-20. Scope: the LLOVES Flask app (ALC). Production is the Fly app `lloves-lms` (`fly.toml`). This is a source review for school IT. It is not a certification, a penetration test, or a production probe.

Standard: [OWASP ASVS 5.0.0](https://github.com/OWASP/ASVS/releases/tag/v5.0.0) (May 2025). Level 1 is all 70 requirements. Level 2 rows below are the ones that matter for staff login, student PIN login, join and catch-up codes, sessions, access control, and student data.

Method: read-only. No application code was changed, nothing was deployed, and no request was sent to production. Evidence is a file and line in this repository, or the local `pip-audit` run recorded below. Where a control lives only on the Fly edge and was not exercised, the row says so.

## Executive summary for school IT

ALC already does several of the basics school IT should expect. Staff sign-in is Google plus an emailed 6-digit code (on by default for the first sign-in, and configurable for every sign-in). Student live join and catch-up PIN tries are rate-limited. Class pages and the game-session log are checked on the server against the signed-in person, and a class in another school is refused. Catch-up PINs are checked against a salted hash. Production turns on HTTPS at the Fly proxy, marks the session cookie Secure and HttpOnly, and sends HSTS, a content-security policy, and `nosniff`. Sentry is configured not to send request bodies, cookies, or query strings, and not to attach a student identity. A local `pip-audit` of `lms/requirements.txt` on this date reported no known vulnerabilities.

The items school IT should treat first:

1. The Google callback accepts a posted credential token **without checking its signature**. For a staff or IT account that has already completed email verification, the default “first sign-in only” setting then opens a session and can replace the stored Google account id. The sign-in page does not turn Google One Tap on; the callback still accepts the field.
2. A catch-up PIN is stored as a PBKDF2 hash **and**, while it is still the system default, as a reversible encrypted copy so a slip can be printed. Same-tenant IT can open those slips, not only the class teacher. After a student or teacher sets a new PIN, the reversible copy is cleared.
3. The staff session is a signed cookie, not a server-side session. Log out clears the browser cookie. A copy of that cookie still works until it ages out (31 days) or the account is archived. The signature uses the library default, HMAC-SHA1, and the cookie name does not use the `__Host-` or `__Secure-` prefix ASVS Level 1 asks for.
4. Several short secrets are guessable relative to ASVS Level 2: the catch-up PIN is 4 digits, and the typed catch-up portal code has one random letter. The staff email code and the live join code are stored in plaintext so they can be shown or emailed. Online guessing is rate-limited.
5. There is no CSRF token. SameSite=Lax is set. Student rejoin uses a high-entropy token in the URL query string. Module-pack upload has no application size cap.

End, Quit, and deleting a test run do different things. **End** (including a run flagged as a test at Start) saves attendance and participation, can open a catch-up link, and then deletes the live answer rows. **Quit** does not save those and deletes that run’s live rows, including presence. **Delete test** is a separate action and refuses a real run.

## How PINs and codes are stored

This is what the code does. It is not a guess about the live database.

| Secret | What the code stores | Where |
| --- | --- | --- |
| Catch-up PIN used to check a guess | Salted PBKDF2-HMAC-SHA256, 60,000 iterations, 8-byte salt from `secrets.token_hex`, stored as `salt$hex`. Comparison is constant-time. A value without `$` never matches. | `lms/participation.py:210`–`221`; column added in `tools/math-game-show/db.py:951`–`955` |
| Default catch-up PIN (until someone replaces it) | The same hash, **plus** a Fernet ciphertext of the 4-digit value (AES-128-CBC and HMAC-SHA256, key derived with HKDF-SHA256 from `DEFAULT_PIN_KEY` or the Flask secret). The slip API decrypts that ciphertext. Setting a new PIN writes a new hash and sets the ciphertext column to NULL. | `lms/default_pin.py:25`–`96`; `lms/school_db.py:37807`–`37812` and `38076`–`38080`; column in `tools/math-game-show/db.py:957`–`961` |
| Staff email sign-in code | Six digits, `secrets.randbelow`, stored **in plaintext** on `users.verification_code`. Cleared to NULL on success. Lifetime 10 minutes. Five guesses, counted before the compare. | `lms/school_db.py:469`; `lms/auth.py:813`–`814`; `lms/school_db.py:5913`–`5927` and `5959`–`5965`; `lms/auth.py:89`–`94` |
| Live join code | Random code from a 31-character alphabet (4 characters, longer when many sessions are active), stored **in plaintext** on `live_class_sessions.session_code` so it can be shown and looked up. Rejects all-digit codes and a blocklist. | `lms/codes.py:27`–`32` and `133`–`179`; `lms/school_db.py:578` |
| Typed catch-up portal code | Five characters: course letter, module digit, **one** random letter, class digit, and a checksum of the first four. Stored **in plaintext** on `catchup_links.portal_join_code`. | `lms/catchup_portal_code.py:74`–`102`; `lms/school_db.py:3861` |
| Catch-up link token | `secrets.token_urlsafe(24)` (192 bits). Stored so the URL can be resolved. It is not a 4-digit PIN. | `lms/participation.py:267`–`269` |
| Student rejoin / visit token | `secrets.token_urlsafe(24)`, stored on the attendee row, also placed in `?v=` and in an HttpOnly cookie. | `lms/school_db.py:4791`; `lms/student_portal.py:302`–`318` and `103`–`120` |
| Staff invite link | Raw token is `secrets.token_urlsafe(32)`. Only SHA-256 is stored. | `lms/staff_invites.py:76`–`90` |

PINs are rejected when they are not 4 digits, when they are two blocked trivial values, or when they equal a current join code (`lms/participation.py:195`–`207`). The catch-up PIN field uses `type="password"` (`lms/templates/student/catchup_landing.html:32`). There is no staff password. Staff identity is Google plus the email code.

## Level 1

| Chapter | Id | Status | Evidence | Note |
| --- | --- | --- | --- | --- |
| V1 Encoding and Sanitization | V1.2.1 | pass | `lms/app.py:1421`; `tools/math-game-show/static/common.js:394`–`419`; `lms/email_service.py:302` | Flask/Jinja escapes HTML by default. Question HTML is escaped before it is inserted. Mail bodies escape text and links. Hand-rolled curriculum HTML is a separate gap (V1.3.1). |
| V1 Encoding and Sanitization | V1.2.2 | pass | `lms/auth.py:470`–`500`; `lms/auth.py:1075`–`1089` | Post-login `next` is decoded and allowlisted. OAuth query parameters are URL-encoded. |
| V1 Encoding and Sanitization | V1.2.3 | pass | `tools/math-game-show/static/common.js:394`–`419` | Dynamic question markup escapes text, including values placed in attributes, before `innerHTML`. |
| V1 Encoding and Sanitization | V1.2.4 | pass | `lms/school_db.py:11030`–`11033`; `lms/engagement_events.py:185`–`216` | Queries use placeholders. Dynamic SQL seen in this review builds `?` lists or fixed predicates, not concatenated user strings. |
| V1 Encoding and Sanitization | V1.2.5 | pass | `lms/` (no `os.system` or `shell=True` in application modules) | No operating-system command built from request data was found in the app. |
| V1 Encoding and Sanitization | V1.3.1 | gap | `lms/question_math.py:86`–`114` | Curriculum HTML is filtered by a local parser and a tag allowlist, not a maintained sanitizer library. `img` `src` is escaped but the scheme is not restricted. |
| V1 Encoding and Sanitization | V1.3.2 | pass | `lms/` (no `eval(` in application Python or JavaScript) | No `eval` or equivalent dynamic execution of request data was found. |
| V1 Encoding and Sanitization | V1.5.1 | pass | `lms/ingest.py:19` and `127` | Cartridge XML uses the standard-library ElementTree parser, which does not resolve external entities. `defusedxml` is not configured. |
| V2 Validation and Business Logic | V2.1.1 | gap | (no security document in the repo) | Validation rules exist in code (codes, PINs, uploads). They are not written down as application security documentation. |
| V2 Validation and Business Logic | V2.2.1 | pass | `lms/codes.py:185`–`200`; `lms/participation.py:45` and `195`–`207`; `lms/catchup_portal_code.py:37`–`63` | Join codes, PINs, and portal codes are checked against an allowlist or a fixed pattern on the server. |
| V2 Validation and Business Logic | V2.2.2 | pass | `lms/auth.py:1483`–`1503`; `lms/school_db.py:37763`–`37788` | Those checks run in the Flask service. Browser `pattern` attributes are extra. |
| V2 Validation and Business Logic | V2.3.1 | pass | `lms/auth.py:897`–`903`; `lms/app.py:10652`–`10685`; `lms/school_db.py:41784`–`41785` | Email verification is required before a staff session when the policy says so. Catch-up answers follow a PIN check. Deleting a test run refuses a real run. This row is only those flows. |
| V3 Web Frontend Security | V3.2.1 | pass | `lms/app.py:1424`; `lms/app.py:11042`–`11045` | `X-Content-Type-Options: nosniff` is set. The session-log download is `text/plain`. |
| V3 Web Frontend Security | V3.2.2 | pass | `tools/math-game-show/static/common.js:418`–`419` | Plain question text is escaped. Avatar rendering avoids `innerHTML` (`lms/static/student_avatars.js:85`). |
| V3 Web Frontend Security | V3.3.1 | gap | `lms/app.py:1323`–`1326`; `lms/student_portal.py:90` and `113`–`120` | Production sets `Secure`. Cookie names are the Flask default and `lloves_live_rejoin`. Neither uses the `__Host-` or `__Secure-` prefix Level 1 requires when `__Host-` is not used. |
| V3 Web Frontend Security | V3.4.1 | pass | `lms/app.py:1446`–`1450` | When `FLASK_ENV=production`, responses set HSTS `max-age=31536000; includeSubDomains` (one year). Local runs do not. The live header was not requested. |
| V3 Web Frontend Security | V3.4.2 | pass | `lms/app.py:1421`–`1451` | No `Access-Control-Allow-Origin` is set, so the app does not send `*`. |
| V3 Web Frontend Security | V3.5.1 | gap | `lms/` (no CSRF token); `lms/app.py:1324` | State-changing forms do not send an anti-forgery token or a non-simple custom header. `SameSite=Lax` is set. That is not the control this requirement names. |
| V3 Web Frontend Security | V3.5.2 | n/a | `lms/app.py:1421`–`1451` | The app does not use CORS preflight as its cross-origin defense. |
| V3 Web Frontend Security | V3.5.3 | gap | `lms/auth.py:1254`–`1260` | Log out is a GET. Ending class, quitting, PIN checks, and test-run delete are POST. |
| V4 API and Web Service | V4.1.1 | pass | `lms/app.py:11042`–`11045`; `lms/app.py:4006`–`4016` | HTML and the session log set a charset. JSON uses Flask’s `application/json`. |
| V4 API and Web Service | V4.4.1 | n/a | `lms/` (no WebSocket server) | Live updates use HTTP polling and server-sent events, not WebSockets. |
| V5 File Handling | V5.2.1 | gap | `lms/modules.py:51`; `lms/app.py:1321` | `IMSCC_MAX_BYTES` is `None`, and that value is Flask’s `MAX_CONTENT_LENGTH`, so the app sets no upload size cap. |
| V5 File Handling | V5.2.2 | pass | `lms/modules.py:668`–`698` | Module packs must be `.imscc` or `.zip`, must start with ZIP magic bytes, and a full check looks for the cartridge manifest. |
| V5 File Handling | V5.3.1 | pass | `lms/modules.py:731`–`759` | The stored name is `course.imscc` under the data directory. It is not mapped as a Python route. |
| V5 File Handling | V5.3.2 | pass | `lms/modules.py:759`; `lms/app.py:1883`–`1890` | Uploads are not saved under the client’s filename. Static files use `send_from_directory`. |
| V6 Authentication | V6.1.1 | gap | `lms/auth.py:52`–`87`; `lms/participation.py:23`–`30` | Join and PIN limits are written in code. There is no security document that defines them for operators. |
| V6 Authentication | V6.2.1 | n/a | `lms/auth.py:1` | No user-set password. The 4-digit PIN is assessed under Level 2 (V6.5 and V11.4.2), not as a password. |
| V6 Authentication | V6.2.2 | n/a | `lms/auth.py:1` | No password to change. Students can replace a catch-up PIN (`lms/school_db.py:37763`). |
| V6 Authentication | V6.2.3 | n/a | `lms/auth.py:1` | No password-change form. A student changing a PIN must already be signed in for that class. |
| V6 Authentication | V6.2.4 | n/a | `lms/auth.py:1` | No password registration. |
| V6 Authentication | V6.2.5 | n/a | `lms/auth.py:1` | No password composition rules. |
| V6 Authentication | V6.2.6 | n/a | `lms/templates/student/catchup_landing.html:32` | No password field. The PIN field is `type="password"`. |
| V6 Authentication | V6.2.7 | n/a | `lms/auth.py:1` | No password field for a password manager to fill. |
| V6 Authentication | V6.2.8 | n/a | `lms/participation.py:217`–`221` | No password normalization. A PIN is hashed exactly as received. |
| V6 Authentication | V6.3.1 | pass | `lms/auth.py:68`–`86` and `90`–`91`; `lms/participation.py:23`–`30`; `lms/app.py:10657`–`10668` | Live join, catch-up PIN guesses, and the email code each have a server-side cap. Quiet join failures share one response. |
| V6 Authentication | V6.3.2 | pass | `lms/app.py:1334`–`1338` | Production refuses to boot if the offline account picker is enabled. There is no shared `admin` password. Access is an allowlist. |
| V6 Authentication | V6.4.1 | gap | `lms/auth.py:92`–`94` and `813`–`814`; `lms/default_pin.py:73`–`88`; `lms/school_db.py:38045`–`38080` | The email code is random, expires in 10 minutes, and is cleared on use. The default PIN is random but becomes the long-term catch-up secret until someone changes it. |
| V6 Authentication | V6.4.2 | pass | `lms/` (no hint or secret-question flow found) | No password hints or knowledge questions. |
| V7 Session Management | V7.2.1 | pass | `lms/auth.py:615`–`629` | The staff cookie is read on the server, then the user row is loaded. An archived user is cleared. |
| V7 Session Management | V7.2.2 | pass | `lms/app.py:1318`–`1326`; `lms/auth.py:571`–`585` | The staff session is a signed cookie minted at login, not a fixed API key. |
| V7 Session Management | V7.2.3 | pass | `lms/school_db.py:4791`; `lms/student_portal.py:103`–`120` | The student rejoin token is `secrets.token_urlsafe(24)` (192 bits) and is checked against the attendee row. The staff cookie is self-contained, so the 128-bit reference-token clause does not apply to it. |
| V7 Session Management | V7.2.4 | pass | `lms/auth.py:504`–`514` and `571`–`585` | Login clears the session, then writes the new login keys. |
| V7 Session Management | V7.4.1 | gap | `lms/auth.py:1254`–`1260`; `lms/app.py:1322` | Log out clears the cookie in that browser. Nothing on the server records the signed cookie as dead, so a copied cookie still verifies until the 31-day lifetime or until `archived_at` is set. |
| V7 Session Management | V7.4.2 | pass | `lms/auth.py:626`–`628` and `869`–`871` | A user with `archived_at` set cannot keep a staff session. The next request clears it. |
| V8 Authorization | V8.1.1 | gap | `lms/school_db.py:15099`–`15125` | Rules exist in `teacher_owns_class` (owner, or IT in the same tenant). They are not in an authorization document. |
| V8 Authorization | V8.2.1 | pass | `lms/auth.py:642`–`705` | Staff, IT, and student routes use server-side decorators. IT-only routes check role or the IT email allowlist. |
| V8 Authorization | V8.2.2 | gap | `lms/app.py:11015`–`11039`; `lms/app.py:10342`–`10362`; `lms/school_db.py:15120`–`15121` | Session logs return the same not-found body for a missing id and for another teacher’s id. PIN slips use that same ownership check, and the check returns true for every IT user in the tenant. The slip helper’s own text says teacher-only. |
| V8 Authorization | V8.3.1 | pass | `lms/app.py:8101`–`8108`; `lms/app.py:4036`–`4039` | Ownership is enforced in the service, not by hiding a button. |
| V9 Self-contained Tokens | V9.1.1 | gap | `lms/auth.py:820`–`836` and `1101`–`1118` | The staff session cookie is MAC-verified by Flask. The same callback accepts a Google credential JWT by decoding the payload only. The docstring says the signature is not checked. |
| V9 Self-contained Tokens | V9.1.2 | gap | `lms/auth.py:820`–`836` | That JWT path has no algorithm allowlist. A caller can choose the payload, including the email and the Google account id. |
| V9 Self-contained Tokens | V9.1.3 | gap | `lms/auth.py:1101`–`1118`; `lms/auth.py:872`–`873` | The JWT path does not use Google’s keys. If the email is already allowlisted, a different account id is written onto the user (`link_google`). |
| V9 Self-contained Tokens | V9.2.1 | gap | `lms/auth.py:820`–`836` | The JWT expiry claim is not read. The staff cookie does use Flask’s 31-day lifetime (`lms/app.py:1322`). |
| V10 OAuth and OIDC | V10.4.1 | n/a | `lms/auth.py:1073`–`1089` | ALC is not an OAuth authorization server. It is a client of Google. The code flow does check a `state` value (`lms/auth.py:1133`–`1140`). |
| V10 OAuth and OIDC | V10.4.2 | n/a | `lms/auth.py:1021` | ALC does not issue authorization codes. |
| V10 OAuth and OIDC | V10.4.3 | n/a | `lms/auth.py:1021` | ALC does not issue authorization codes. |
| V10 OAuth and OIDC | V10.4.4 | n/a | `lms/auth.py:1075`–`1081` | ALC does not issue grants. The login client asks Google for the authorization-code flow (`response_type=code`). |
| V10 OAuth and OIDC | V10.4.5 | n/a | `lms/auth.py:1021` | ALC does not issue refresh tokens to public clients. |
| V11 Cryptography | V11.3.1 | pass | `lms/default_pin.py:25`–`47` | PIN slips use Fernet (AES-CBC with HMAC), not ECB and not RSA PKCS#1 v1.5. |
| V11 Cryptography | V11.3.2 | pass | `lms/default_pin.py:25`–`47` | Fernet is an authenticated encryption construction. The app does not implement a custom cipher. |
| V11 Cryptography | V11.4.1 | gap | `lms/app.py:1318`–`1326` | PIN hashing uses SHA-256. The Flask session signer is ItsDangerous 2.2’s default, HMAC-SHA1 (confirmed in this environment’s `itsdangerous.signer.Signer`; the app does not override `digest_method`). MD5 was not found as a security hash. |
| V12 Secure Communication | V12.1.1 | gap | `fly.toml:53`–`55` | The repo forces HTTPS at the proxy. TLS version and cipher suite are not set here, and this review did not open a connection to production. |
| V12 Secure Communication | V12.2.1 | pass | `fly.toml:53`–`55`; `lms/app.py:1325`–`1333` | Fly `force_https` is true. Production cookies are Secure, and the app prefers the `https` scheme. |
| V12 Secure Communication | V12.2.2 | gap | `fly.toml:4` | The public URL is `https`. The certificate was not fetched, so public trust was not verified. |
| V13 Configuration | V13.4.1 | pass | `.dockerignore:1`; `lms/Dockerfile:18` | The image build context excludes `.git`. |
| V14 Data Protection | V14.2.1 | gap | `lms/student_portal.py:302`–`318`; `lms/participation.py:175`–`182` | PINs and the staff email code are posted in the body. The student visit token is put in the `v` query parameter. The catch-up link token is a path segment. |
| V14 Data Protection | V14.3.1 | pass | `lms/auth.py:1254`–`1260`; `lms/student_portal.py:123`–`130` | Log out clears the session and the rejoin cookie, then redirects. `Clear-Site-Data` is not set. |
| V15 Secure Coding and Architecture | V15.1.1 | gap | `lms/requirements.txt` | No document sets a deadline for patching vulnerable libraries. |
| V15 Secure Coding and Architecture | V15.2.1 | gap | `lms/requirements.txt`; pip-audit note below | Requirements use minimum versions, not a lockfile. The scan on this date found no known vulnerabilities, but there is no remediation clock to be inside. |
| V15 Secure Coding and Architecture | V15.3.1 | gap | `lms/app.py:11135`–`11140` | Starting a live session returns the session row to the owning staff member (that includes the join code, which the screen must show). Responses are not reduced to a field allowlist. PIN hashes are not included in the roster PIN table (`lms/school_db.py:37964`–`38042`). |

## Level 2 reviewed

These are not the full Level 2 catalogue. They are the requirements that match authentication, sessions, access control, and student data.

| Chapter | Id | Status | Evidence | Note |
| --- | --- | --- | --- | --- |
| V3 Web Frontend Security | V3.3.2 | pass | `lms/app.py:1324`; `lms/student_portal.py:116` | Session and rejoin cookies are `SameSite=Lax`. |
| V3 Web Frontend Security | V3.3.4 | pass | `lms/app.py:1323`; `lms/student_portal.py:115` | Both cookies are HttpOnly. |
| V3 Web Frontend Security | V3.4.3 | gap | `lms/app.py:1433`–`1444` | A CSP is sent, including `frame-ancestors 'none'`. `script-src` and `style-src` include `'unsafe-inline'`. Live-media pages relax framing to the same origin (`lms/app.py:1891`–`1903`). |
| V3 Web Frontend Security | V3.4.4 | pass | `lms/app.py:1424` | `X-Content-Type-Options: nosniff` on responses. |
| V3 Web Frontend Security | V3.4.5 | pass | `lms/app.py:1426`–`1428` | `Referrer-Policy: strict-origin-when-cross-origin`. |
| V3 Web Frontend Security | V3.4.6 | pass | `lms/app.py:1442` | Default CSP is `frame-ancestors 'none'`, with `X-Frame-Options: DENY`. |
| V6 Authentication | V6.3.3 | gap | `lms/auth.py:102`–`108` and `137`–`155`; `lms/test_security.py:137`–`155` | Email 2-step is a second factor, but production does not force it on every Google sign-in unless Admin Settings or `REQUIRE_PRIVILEGED_MFA` say so. Students have no second factor. |
| V6 Authentication | V6.3.4 | gap | `lms/auth.py:1101`–`1118`; `lms/app.py:2022` | The code flow checks `state`. The unsigned credential field is a second sign-in path. The landing page sets one-tap auto prompt off; the callback still accepts the field. |
| V6 Authentication | V6.5.1 | pass | `lms/school_db.py:5959`–`5965` | A successful email code is deleted. A catch-up PIN is meant to be reused; it is not a one-time code. |
| V6 Authentication | V6.5.2 | gap | `lms/school_db.py:5919`–`5923`; `lms/school_db.py:578`; `lms/participation.py:210`–`214` | The email code, live join code, and portal code are stored in plaintext and are far under 112 bits. The PIN check uses a salted password hash. The default PIN is also stored encrypted so it can be decrypted. |
| V6 Authentication | V6.5.3 | pass | `lms/auth.py:813`; `lms/codes.py:105`–`115`; `lms/default_pin.py:81` | Email codes, join codes, and default PINs are drawn with `secrets`. |
| V6 Authentication | V6.5.4 | gap | `lms/participation.py:45` and `203`; `lms/catchup_portal_code.py:100`–`102` | A 4-digit PIN is about 13 bits. The portal code adds one random letter (about 27 choices) to public course, module, and class digits. The 6-digit email code and a 4-character live join code meet the requirement’s “6 digits or 4 alphanumeric characters” example. |
| V6 Authentication | V6.5.5 | pass | `lms/auth.py:92`–`94` | The email code lifetime is 10 minutes, which is this requirement’s maximum. |
| V6 Authentication | V6.6.1 | n/a | `lms/email_service.py:342` | The second factor is email, not SMS or a phone call. |
| V6 Authentication | V6.6.3 | pass | `lms/auth.py:90`–`91`; `lms/school_db.py:5930`–`5956` | The email code allows five guesses, reserved before the compare. |
| V6 Authentication | V6.8.2 | gap | `lms/auth.py:820`–`836` and `1101`–`1118` | Google’s code flow uses the token endpoint and userinfo. The credential-JWT branch does not check a signature. |
| V7 Session Management | V7.1.1 | gap | `lms/app.py:1322` | The permanent session lifetime is 31 days in code. There is no document that justifies inactivity and absolute lifetimes. |
| V7 Session Management | V7.3.1 | gap | `lms/app.py:1322`; `lms/auth.py:585` | No shorter idle timeout. The permanent cookie’s lifetime is 31 days and is refreshed when the session is saved. |
| V7 Session Management | V7.3.2 | gap | `lms/app.py:1322` | There is no absolute cap measured from the original sign-in, separate from that sliding 31 days. |
| V7 Session Management | V7.4.4 | gap | `lms/templates/staff/home.html:30`; `lms/templates/it/dashboard.html:16` | Staff and IT shells show Log out. A visible control on every student view was not confirmed. |
| V7 Session Management | V7.6.1 | gap | `lms/auth.py:571`–`585` | After Google sign-in, the app session is independent. It does not end when the Google session ends. |
| V8 Authorization | V8.4.1 | pass | `lms/school_db.py:15118`–`15122`; `lms/test_security.py:105`–`135` | A class in another tenant is not owned by this school’s teacher or IT user. The session-log route uses the same check (`lms/app.py:11036`–`11039`). |
| V11 Cryptography | V11.4.2 | gap | `lms/participation.py:210`–`214`; `lms/default_pin.py:92`–`96` | The PIN hash is PBKDF2-HMAC-SHA256 at 60,000 iterations. Current password-hashing guidance is much higher (OWASP has cited 600,000 for this construction). Default PINs are also reversible. |
| V11 Cryptography | V11.5.1 | gap | `lms/codes.py:133`–`179`; `lms/default_pin.py:81`; `lms/participation.py:267`–`269` | Join codes, PINs, portal codes, and the email code are from a CSPRNG but under 128 bits. Catch-up link tokens, visit tokens, and OAuth `state` values are `token_urlsafe` of 24 or 32 bytes. |
| V14 Data Protection | V14.1.1 | gap | (no data-classification document) | Student names, PINs, join codes, and visit tokens are sensitive. They are not classified in a document with protection levels. |
| V14 Data Protection | V14.3.2 | gap | `lms/app.py:10384`; `lms/auth.py:759`–`760` | PIN slips send `Cache-Control: no-store`. That header is not set on every authenticated page. |
| V14 Data Protection | V14.3.3 | gap | `lms/student_portal.py:380`–`398` | The staff/student session cookie also holds the join code (`student_live_code`) and a first name (`student_codename`). The rejoin cookie is a session token, which this requirement allows. |
| V16 Logging and Error Handling | V16.2.5 | gap | `lms/email_service.py:342`–`349`; `lms/sentry_wire.py:346`–`372` and `488`–`501` | Application log lines reviewed do not print a PIN, a join code, or a student name. Scoring JSONL stores a student id, not a name (`tools/math-game-show/db.py:5961`–`5970`). Sentry drops bodies, cookies, and query strings, hashes staff email, and does not set a student user. Two leftovers: a successful verification email logs the staff address (`lms/email_service.py:141`), and if `ALLOW_DEV_VERIFICATION_CODE` is on and mail is not configured, the email code is written to the log. That flag is not set in `fly.toml`. The on-page code is refused when `FLASK_ENV=production` (`lms/email_service.py:35`–`43`). There is no scrubber that would remove a PIN or join code if one appeared inside an exception string. |
| V16 Logging and Error Handling | V16.3.1 | gap | `lms/auth.py:587`–`597` | Successful staff login is written to the access audit. Failed Google, email-code, join, and PIN attempts were not found as audit events. |
| V16 Logging and Error Handling | V16.5.1 | gap | `lms/app.py:530`–`538` | Unexpected API exceptions are returned as `str(exc)` with status 500. Flask debug is not turned on by production. That still sends internal exception text to the caller. |

## Dependency scan

`python3 -m pip_audit -r lms/requirements.txt` on 2026-10-20 reported **no known vulnerabilities**. It resolved the minimum pins to current releases, including Flask 3.1.3, Werkzeug 3.1.9, Jinja2 3.1.6, requests 2.34.2, gunicorn 26.2.0, cryptography 50.0.2, sentry-sdk 2.72.0, and itsdangerous 2.2.0. The image build installs that same requirements file (`lms/Dockerfile:15`–`16`). There is no lockfile, so a later build can resolve different versions.

## Data deletion: End, Quit, and test runs

- **End** (`POST /staff/class/<id>/end-live`) always saves attendance and participation, including a run that was marked a test at Start. It does not honor an old “don’t save” field. It then closes the live session, which drops the join code from use and deletes ephemeral votes and responses after a results snapshot (`lms/app.py:4018`–`4052`; `lms/school_db.py:40996`–`41020` and `41106`–`41177`; `lms/school_db.py:39131`–`39156`).
- **Quit** (`POST /staff/class/<id>/quit-live`) saves neither attendance nor participation and does not create a catch-up link. It deletes that active run’s responses, attendees, engagement, notes, and presence (`lms/app.py:4095`–`4120`; `lms/school_db.py:41168`–`41204`).
- **Delete test** (`POST /api/classes/<id>/live-sessions/<id>/delete-test`) deletes one test run across school rows, tracker rows, and presence. It refuses a run that is not a test, and it refuses a run that was not created as a test once it has real activity. The route checks class ownership first (`lms/app.py:11183`–`11194`; `lms/school_db.py:41754`–`41785`).

Roster rows and gradebook history are not what Quit deletes. End is what writes the class-day column.

## Logging and Sentry

Production Sentry starts only when `SENTRY_DSN` is set. `send_default_pii` is false, local variables are omitted, and request bodies are never attached (`lms/sentry_wire.py:488`–`501`). `before_send` removes the body, cookies, and query string, and masks invite and student-seat paths (`lms/sentry_wire.py:346`–`372` and `295`–`308`). Staff events get an id and a short HMAC of the email, not the raw address (`lms/sentry_wire.py:625`–`640`). Students do not get a Sentry user (`lms/sentry_wire.py:512`–`513`). Action logs are fixed phrases plus ids (`lms/sentry_wire.py:852`–`873`).

The class session log the teacher downloads is owner-checked and stores point events by student id (`lms/app.py:11015`–`11039`; `tools/math-game-show/db.py:5961`–`5970`).

## Prioritized gaps

### High

1. **Unverified Google credential on the login callback.** Anyone who can POST `credential` can supply an allowlisted email. After the account has passed email verification once, the default policy skips the email code and `link_google` can replace `google_sub`. Fix: reject that field, or verify the JWT with Google’s keys, audience, issuer, and expiry before trusting it. (`lms/auth.py:820`–`836`, `1101`–`1118`, `872`–`873`)
2. **Default catch-up PINs are reversible, and same-tenant IT can read the slips.** The value used at sign-in is a hash. Until the PIN is changed, a Fernet copy can be decrypted with the app secret, and the slip routes allow every IT user in the tenant because `teacher_owns_class` returns true for role `it`. A database copy plus the app secret reveals every PIN that is still a default. Fix: confirm with school IT whether IT should see slips; if not, restrict the routes to the class owner. Prefer a one-time display that does not keep a decryptable copy, and require a student-chosen PIN. (`lms/default_pin.py:45`–`96`; `lms/app.py:10342`–`10362`; `lms/school_db.py:15120`–`15121`)
3. **Staff sessions are not revoked on log out and last 31 days.** The cookie is self-contained, signed with HMAC-SHA1, and not named with `__Host-` / `__Secure-`. A stolen cookie survives log out. Fix: a server-side session id (or a per-user session version checked on each request), a SHA-256 signer, the `__Host-` cookie prefix, and a shorter absolute lifetime. (`lms/auth.py:1254`–`1260`; `lms/app.py:1318`–`1326`)

### Medium

4. **Short student secrets.** The PIN is 4 digits (~13 bits) and the typed portal code has one random letter. Online guesses are capped (join quiet-limit 80 per IP in 10 minutes; PIN IP limit 20 and 5 per link in 15 minutes; email code 5 guesses). Fix: keep the caps, lengthen the portal code’s random part, and treat the default PIN as a one-time activation code (V6.4.1). (`lms/participation.py:23`–`30`; `lms/catchup_portal_code.py:100`–`102`; `lms/auth.py:68`–`86`)
5. **Plaintext storage of the email code and join codes.** Both are under 112 bits (V6.5.2). The email code is short-lived and deleted on success, which limits the window. Join codes are plaintext because the teacher must display them. Fix: store the email code as a salted hash; accept plaintext join codes only while that session is on screen, or store a hash and keep the display copy in the staff session. (`lms/school_db.py:5919`–`5923`; `lms/school_db.py:578`)
6. **No CSRF token.** SameSite=Lax blocks cross-site POST cookies in current browsers. Log out is still a GET. Fix: a CSRF token or a custom header on state-changing requests, and POST for log out. (`lms/app.py:1324`; `lms/auth.py:1254`)
7. **Session and catch-up secrets in the URL.** `?v=` carries the visit token. The catch-up token is in the path, so it lands in access logs and Referer unless stripped. Sentry strips query strings; ordinary proxy logs were not reviewed. (`lms/student_portal.py:302`–`318`)
8. **No application upload size limit** for module packs (`lms/modules.py:51`). Disk on the Fly volume is the backstop.
9. **CSP allows inline scripts** (`lms/app.py:1436`), which weakens XSS containment. The curriculum sanitizer is hand-rolled (V1.3.1).
10. **API 500s return `str(exc)`** (`lms/app.py:530`–`538`). Failed sign-in is not in the audit log (V16.3.1). Email 2-step is not required on every later Google sign-in (V6.3.3), which makes gap 1 worse.
11. **PBKDF2 at 60,000 iterations** is below current guidance for password hashing, and it does not protect the reversible default PIN (V11.4.2).

### Low

12. **No written security, logging, session, or dependency policy** (V2.1.1, V6.1.1, V7.1.1, V8.1.1, V14.1.1, V15.1.1, V15.2.1). The controls exist in code. School IT does not have a document that sets patch deadlines or log rules.
13. **Dev-only email-code logging.** If `ALLOW_DEV_VERIFICATION_CODE` is enabled and mail is not configured, the code and the account name are logged (`lms/email_service.py:347`–`348`). Production `fly.toml` does not set the flag, and the verify page hides the code in production. Do not set that flag on the Fly app.
14. **TLS version and the live certificate were not checked.** Fly `force_https` is set. This review made no production call (V12.1.1, V12.2.2).
15. **`Cache-Control: no-store` is not global** on authenticated HTML (V14.3.2). PIN slips do send it.
16. **pip-audit was clean** on 2026-10-20, and requirements are unpinned `>=` ranges. A later image build can move versions without a lockfile.

## Counts

| Set | pass | gap | n/a | Rows |
| --- | --- | --- | --- | --- |
| Level 1 (all 70) | 33 | 22 | 15 | 70 |
| Level 2 reviewed | 10 | 19 | 1 | 30 |
| **Total in this report** | **43** | **41** | **16** | **100** |

Several Level 1 gaps are one defect counted under each requirement it fails (the unsigned JWT is V9.1.1, V9.1.2, V9.1.3, and V9.2.1). The prioritized list above is the set to file as follow-ups.
