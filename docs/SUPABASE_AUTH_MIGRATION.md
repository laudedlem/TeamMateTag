# Supabase Auth Migration

This project now uses Supabase Auth for account creation, login, email verification,
password reset, and password changes, while keeping gameplay/profile identity in the
existing app tables.

## Current model

1. Supabase Auth owns:
   - email/password sign up
   - email/password sign in
   - verification emails
   - recovery emails
   - password reset completion

2. App tables still own gameplay identity and stats:
   - `guests`
   - `users`
   - `bp_runs`
   - `fr_results`
   - `dr_results`
   - `friendships`
   - `friend_requests`
   - `dr_friend_challenges`

3. The link between auth and gameplay lives in `users.auth_user_id`.

4. The app keeps a lightweight server-side session in `app_sessions`:
   - `tt_session` cookie in the browser
   - maps back to `guest_id` and `auth_user_id`
   - lets the existing API stay server-authoritative without rewriting the whole game

## What this preserves

- guest bootstrap flow
- saved profile stats
- friends
- Division Rivalry matchmaking history
- leaderboards

## Implemented

- `/api/account/register` now creates auth users through Supabase Auth
- `/api/account/login` signs in through Supabase Auth
- `/api/account/logout` clears the app session cookie and server session row
- `/api/account/reset_password` sends Supabase recovery email
- `/reset-password` completes the password reset through Supabase JS
- `/api/account/resend_verification` resends signup verification mail
- `/api/account/delete` verifies password through Supabase Auth and deletes both auth + linked app data
- friends and friend challenges now require a signed-in session instead of trusting a posted `guest_id`
- the app no longer verifies passwords against app-managed hashes during normal account use

## Still worth doing next

### Short term

- rotate the exposed database password and update `DATABASE_URL`
- set up a real inbox for `support@teammatetag.com`
- test the verification and recovery flows on the live Vercel URL end to end
- add rate limiting around auth-heavy endpoints if traffic starts to climb

### Medium term

- remove unused legacy password columns once you are comfortable there are no old accounts left to migrate
- move more profile/account-edit endpoints to session-first access patterns
- consider swapping the lightweight app session for fuller Supabase session handling if you later build a richer SPA shell

### Longer term

- enable stronger auth features like social login only if they clearly help the product
- add audit/admin tooling for account deletes and moderation actions

## Risks to handle carefully

- preserving existing guest-linked stats
- preserving friend relationships
- avoiding duplicate profiles when a guest upgrades on two devices
- making sure deletion removes both auth identity and linked gameplay identity

## Definition of done

- account creation and login happen through Supabase Auth
- existing profile stats survive migration
- password reset works
- email verification works
- friends and challenges require signed-in sessions
- app code no longer stores or verifies password hashes directly during normal auth flows
