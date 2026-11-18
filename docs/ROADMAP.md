# Roadmap

Each stage ends with a working build. A stage is done only when its exit
criteria pass.

Work for a stage happens on its own branch and is merged into `main` when the
stage is done. `main` always holds the last finished stage.

| Stage | Branch |
|---|---|
| 0.1 Core | `stage/0.1-core` |
| 0.2 Local reading | `stage/0.2-local-reading` |
| 0.3 Style and context | `stage/0.3-style-context` |
| 0.4 History | `stage/0.4-history` |
| 0.5 Media | `stage/0.5-media` (created when the stage starts) |
| 0.6 Follow mode and automatic suggestions | `stage/0.6-follow` (created when the stage starts) |
| 0.7 Local model | `stage/0.7-local-ai` (created when the stage starts) |
| 0.8 Release | `stage/0.8-release` (created when the stage starts) |

## 0.1 Core

Hotkey to pasted reply, using a screenshot of the chat as model input.

- [x] `hotkey`: register the default global hotkey, ignore it outside WhatsApp
- [x] `capture`: capture the WhatsApp window itself (not a screen region), crop
      to the open chat, downscale to about 1300 px
- [x] `llm`: send the image with the system prompt, validate the JSON reply
- [x] `ui/panel`: summary, message checkboxes, 2 or 3 options, quick
      instruction, regenerate without capturing again
- [x] `paste`: check window and chat are still the same, save and restore the
      clipboard, paste, never press Enter
- [x] `privacy`: private contacts list
- [x] `ui/tray`: pause and quit
- [x] `i18n`: every interface string in translation files, pt-BR and en
- [x] Fixtures: fictional screenshots (short, long, group, dark mode, image,
      audio, quoted reply) in both languages
- [x] `reader`: contact name and chat bounds through accessibility
- [x] `config`: minimal settings file and API key in the Credential Manager
- [x] `evals`: scripts for the model comparison and the blind review
- [x] Model comparison on 30 fictional chats to pick the default model

Exit criteria:

- 10 chats in a row without an error
- panel filled within 5 seconds
- text is never pasted into a chat other than the captured one

## 0.2 Local reading

Read the chat as text through accessibility. The screenshot becomes the
fallback.

- [x] `reader`: message text with author and time, on top of the contact name
      and chat bounds from 0.1. Must rely on structure and position, not on
      translated labels
- [x] `privacy`: mask CPF, CNPJ, card numbers, Pix keys and similar before
      anything is sent, with Brazilian and international formats
- [x] Text mode as default input, automatic fallback to screenshot with a
      notice to the user
- [x] `ui/settings`: settings window with hotkey recorder and conflict check
- [x] `config`: read and write every setting in `config.toml` from the settings
      window
- [x] Quick instruction buttons in the panel, editable in the settings window
- [x] Panel position and start with Windows
- [x] Check WhatsApp Web in the browser

Exit criteria:

- suggestion quality in text mode matches screenshot mode
- fictional sensitive data always leaves masked

## 0.3 Style and context

- [x] `database`: encrypted SQLite under `%LOCALAPPDATA%`, key protected by
      DPAPI, schema migrations
- [x] `style/parser`: both export formats, multi-line messages, names with
      emoji, omitted media, other date formats, `.zip` exports read in memory
- [x] `style/sampler`: keep only the user's messages, sample across chats and
      periods
- [x] `style/profile`: extract, store and update the profile, one per language
- [x] Passive learning from the user's own messages seen on screen
- [x] `ui/style_view`: import export file, review profile, own notes, restore
      default
- [x] `context`: `knowledge.md`, contact labels, style variation per label
- [x] Style button in the panel and the tray, contact label in the panel

Exit criteria:

- suggestions are recognised as the user's own in a blind test
- missing data produces a `[confirm: ...]` marker instead of an invented value
- no third-party message reaches the model or the saved profile

## 0.4 History

- [x] `memory/store`: history tables in the encrypted database, full-text index,
      retention period, delete per contact and delete all
- [x] `memory/merge`: overlap stitching without duplicates, edited messages,
      gap detection, days from the day separators
- [x] `memory/identity`: tell contacts with the same name apart before any
      memory is used
- [x] `memory/loader`: one-click load by scrolling and reading, stops on any
      user input, focus loss or a window over the chat. Also loads chats for the
      style screen
- [x] `memory/recall`: contact memory summary, earlier messages found by search
- [x] History controls in the panel, the settings and the style screen

Exit criteria:

- a suggestion gets right a detail agreed weeks earlier
- memory never shows up on the wrong contact, including contacts with the
  same name

## 0.5 Media

- [ ] Pending photos and video thumbnails sent as images, cropped from the
      WhatsApp window, one-to-one chats only, with a setting to turn it off
- [ ] Link previews read from the screen and masked. Links are never opened
- [ ] Audio and video length; WhatsApp's own transcript when it is on screen

Exit criteria:

- with fictional photos, links, videos and audio, suggestions never contradict
  the media
- no image leaves for a group, a private contact or a message that is not
  pending

## 0.6 Follow mode and automatic suggestions

- [ ] Follow mode: read the chat when it opens, for contacts already in scope,
      with a tray indicator
- [ ] Automatic suggestions for selected labels or all contacts, with a daily
      limit, without taking the focus

Exit criteria:

- automatic suggestions never fire for the user's own last message, private
  contacts, groups or twice for the same message
- an hour of use with follow mode on leaves no duplicate and reads no contact
  out of scope

## 0.7 Local model

- [ ] `local_llm`: local engine and model download
- [ ] Cloud, hybrid and local modes; background tasks on the local model

Exit criteria:

- with the network off, the app suggests and tries no external call
- a contact memory is built on the machine on a PC with 8 GB of memory

## 0.8 Release

- [ ] `server/`: relay for product accounts, no content stored or logged,
      short-lived tokens, per-account quota
- [ ] `auth`: account sign-in, own API key as the advanced option
- [ ] One-screen first run
- [ ] Complete tray menu
- [ ] Signed installer and signed updates
- [ ] Terms of use and privacy policy, pt-BR and en

Exit criteria:

- a non-technical person installs, signs in and gets a first suggestion in
  under 2 minutes

## Later

- Suggestions fully on the local model for machines with more memory
- Usage metrics
- Mobile, Android first
