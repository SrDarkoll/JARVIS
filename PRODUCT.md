# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

JARVIS is designed for any person who installs it as a personal desktop assistant. The primary experience serves one local owner while retaining clearly separated secondary profiles for guests or additional users.

## Product Purpose

JARVIS helps people communicate with an AI assistant, inspect useful local context, and carry out permitted actions from one desktop experience. Text and voice are equal first-class inputs. Success means the user can understand what JARVIS heard or received, what it intends to do, whether confirmation is required, what actually happened, and how to recover when a capability is unavailable.

## Positioning

JARVIS is a privacy-first local assistant with a cinematic identity. Local data, memories, automation policy, and runtime state remain on the user's computer by default. Cloud AI providers and external accounts extend the product but are optional, explicit, and visible to the user.

## Operating Context

- Windows is the primary desktop target, commonly through the local web interface or WebView2 desktop shell.
- The backend is a local Python and Quart service; the interface is a vanilla JavaScript web application.
- Users may interact by text, push-to-talk voice, passive wake-word voice when explicitly enabled, full-duplex voice, or live screen features.
- Hardware, permissions, AI providers, Spotify, and other integrations may be absent or unavailable on a given installation.
- The product supports local profiles, sensitive-action confirmation, capability reporting, and technical diagnostics.

## Capabilities and Constraints

- The stable core must remain useful without voice biometrics, vision, RAG, plugins, briefing, Telegram, Spotify developer credentials, or system tools.
- Text interaction remains available when microphone access, speech recognition, or audio capture fails.
- Microphone and screen permissions are requested only from explicit user actions that explain their purpose.
- Provider, network, and permission failures use controlled user-facing language and do not expose secrets, stack traces, raw request data, or internal identifiers.
- Sensitive or destructive actions remain separate from ordinary authorization and require explicit confirmation.
- The interface supports Spanish and English consistently.
- The visual experience must adapt to desktop, tablet, and mobile-sized web viewports without removing core functionality.

## Brand Commitments

- Preserve the J.A.R.V.I.S. name, reactor motif, dark technical HUD identity, cyan energy accents, and concise assistant voice.
- Cinematic presentation supports the task instead of delaying or hiding it.
- Technical terminology is appropriate inside diagnostics, but primary actions use complete, understandable language.
- The experience should feel capable, calm, precise, and trustworthy rather than noisy or cryptic.

## Evidence on Hand

- The repository contains the working Quart backend, vanilla JavaScript HUD, local voice stack, capability and setup endpoints, profile handling, Spotify integration, and automated regression suite.
- The current interface has been tested locally at 1440 × 900, 1024 × 768, and 390 × 844. Reproduction evidence is stored under `scratch/dogfood-jarvis-ux/` and is deliberately excluded from release artifacts.
- No external customer claims, testimonials, performance guarantees, or privacy certifications are available and none should be invented.

## Product Principles

1. Local and understandable by default.
2. A usable fallback for every optional capability.
3. User intent before cinematic presentation.
4. Truthful progress, confirmation, and results.
5. One coherent experience across input methods and screen sizes.

## Accessibility & Inclusion

- All core workflows must be usable with keyboard and text input.
- Dynamic state changes require meaningful accessible names and live-region behavior without stealing focus.
- Reduced-motion preferences disable nonessential animation while preserving state communication.
- Spanish and English update visible copy, document language, accessible names, and recovery guidance together.
- Color is never the only indicator of status, risk, or completion.
