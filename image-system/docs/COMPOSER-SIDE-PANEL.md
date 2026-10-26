# Image Composer display modes

Popup remains the default. The icon-only **Dock Image Composer** button
places the existing Composer and native message form beside the conversation.
Undock restores the Popup presentation. The close button hides Composer controls
and leaves the normal message input and a compact Image Composer launcher.

There is one native form, textarea, Composer component and submission path. A
stable React portal target moves between the original input outlet and a real
flex-layout panel. Moving it preserves DOM identity, selection state and draft
text. The main chat resizes; the panel never covers the conversation. Prompt and
Details dialogs retain their existing body portals and modal behavior.

## Width and direction

The panel defaults to 384px, ranges from 320–560px, and leaves at least 480px for
chat plus a 12px gap. Other open native sidebars count against available space.
Drag the inner edge or focus its separator and use arrow keys (Shift: 40px;
otherwise 10px), Home (minimum), End (safe maximum), Enter (default).
Left and Right mean physical position, independently of Hebrew/English content.

If a 320px panel plus the chat reserve does not fit, the same form appears in
Popup mode with a clear notice. This temporary fallback never overwrites the
preferred mode, side, open state or width. The fallback caps the expanded controls
scroll area at 20dvh/200px and keeps an opaque layout header; the existing chat
padding reservation leaves messages scrollable above the input. Widening the window restores the
panel. The navigation sidebar keeps its existing 260–520px range and 292px
default; neither sidebar writes the other's preferences.

Local preference key: `aag.image-composer.layout.v1`. Pointer movement updates
layout once per animation frame, while storage is written at gesture completion.
ResizeObserver watches layout availability; there is no new polling loop.

## Drafts and scope

Native unsent prompt storage keeps its existing format and debounce. It is
scoped to the displayed native workspace/thread slugs, preventing the old
conversation from writing its draft under the next URL during asynchronous
navigation. The loaded ChatContainer remains keyed by the same stable slugs.

Mode and control selections use a bounded 24-scope, page-memory UI draft cache.
Returning during navigation restores selections; a full reload starts fresh
transient controls as before. Existing Expert and language preferences remain
authoritative. No job, progress, recovery or artifact state enters this cache.
No database ID, API route, socket identity, ordinal or filename is rewritten.
During the native conversation handover, the old form is inert until its scope
matches the route. The generation pipeline is unchanged.

## Validation

`node --test tests/composer-layout.test.js` covers preference validation,
storage failure, clamping and physical keyboard direction. The permanent
`tools/composer-dock-browser-acceptance.py` runs only against an explicitly
owned private AnythingLLM container/database on localhost:3111. It covers DOM
identity, form state, scope switching, normal stable-slug submission, failure,
layout, restart, dialogs, bidi, one-frame resize work and idle behavior. Its
catalog/READY and terminal-stream fixtures are labeled; it makes no engine
calls. Existing sidebar ordinal and live artifact suites remain required.

Run one browser at a time with thermal monitoring. A new candidate still
requires visual review and separate explicit deployment approval.

## Chat presentation after manual review

Dock/Undock is the only presentation-mode control. Existing saved `mode`
preferences remain compatible. Panel side appears only while actually docked.
Expanded controls use an upward collapse chevron; collapsed controls use a
downward expand chevron, explicitly independent of text direction.

The existing native model picker and settings menu occupy a compact, non-scrolling
chat header. Model resolution, refresh events and provider selection remain native.
The model label uses readable contrast, ellipsis and a full hover/focus tooltip.
Only the bounded conversation viewport scrolls messages; it owns symmetric gutters
of `clamp(16px, 3cqi, 32px)`. Generated cards use available content width, preserve
image aspect ratio and wrap their action row in its existing physical order.

`tools/composer-refinement-browser-acceptance.py` verifies the real native layout
at Popup, both dock positions/widths, wide navigation and constrained sizes. It
checks native historical image scaling, gutters, header position during actual
history scrolling, chevron semantics, model refresh and full-label accessibility.
A labeled model-name response fixture exercises a long mixed-direction label.
It does not submit a model request or alter routing or historical artifacts.

## New conversation lifecycle

Home's empty conversation has the same layout host and fixed model header as
an existing thread. Docking needs no message or persisted chat. Its temporary
Composer draft is distinct from the workspace's default conversation and is
never used as an API, socket, database or artifact identifier.

On the first successful native thread creation, selections move once to the
stable workspace/thread slug. An existing stable draft wins. The old draft is
retired, so a late update cannot recreate it or populate another thread. Updates
are saved synchronously in page memory before navigation. Attachment-created
Home threads and bare-workspace first submissions follow the same handoff.
Prepared ADVANCED instructions and in-memory attachments accompany the normal
native first-message submission. Sent text clears using native prompt semantics.
Expert preferences and panel placement retain their existing persistence.

The basic Composer selection is labeled AUTO; there is no separate BASIC mode.
`tests/composer-draft-handoff.test.js` checks ownership, once-only transfer, stable
draft precedence, bounded memory and synchronous updates.
`tools/new-thread-composer-browser-acceptance.py` tests the real Home-to-thread
transition, zero-message docking, AUTO/basic and ADVANCED, Expert overrides,
selections, closed/side/width preservation, scope switching and reload behavior.
Only generation-boundary responses are fixtures; it never calls an image engine.
