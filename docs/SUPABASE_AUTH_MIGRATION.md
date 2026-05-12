# Supabase Auth Migration Plan

This project currently uses app-managed accounts stored in `users` plus guest-linked profiles in `guests`.
That is workable for playtesting, but it should be replaced before a broader public launch.

## Goal

Move email and password authentication to Supabase Auth while keeping:

- guest bootstrap flow
- saved profile stats
- friends
- Division Rivalry matchmaking history
- leaderboards

## Target model

1. Supabase Auth owns:
   - email/password login
   - password reset
   - email verification
   - sessions

2. App tables keep gameplay data:
   - `guests`
   - `bp_runs`
   - `fr_results`
   - `dr_results`
   - `friendships`
   - `friend_requests`
   - `dr_friend_challenges`

3. A profile table links gameplay identity to auth identity:
   - `profiles.profile_id`
   - `profiles.auth_user_id`
   - `profiles.display_name`
   - `profiles.username`

## Recommended steps

### Phase 1

- Create `profiles` table keyed by Supabase Auth user id
- Mirror existing `users.username`, `users.email`, and `guests.display_name` into `profiles`
- Add a migration script that maps current `users.user_id` to `profiles.auth_user_id`

### Phase 2

- Enable Supabase Auth email/password
- Replace `/api/account/register` and `/api/account/login`
- Use Supabase-issued session tokens on the client
- Keep guest bootstrap for anonymous players

### Phase 3

- Add guest-to-account upgrade flow
- On upgrade, attach guest stats/history to the new auth user profile
- Preserve username uniqueness at the app level

### Phase 4

- Add password reset
- Add email verification
- Add session-aware auth checks on friend and profile actions

### Phase 5

- Remove password hash and salt storage from the app `users` table
- Either remove the table entirely or downgrade it to non-auth profile metadata

## Risks to handle carefully

- preserving existing guest-linked stats
- preserving friend relationships
- avoiding duplicate profiles when a guest upgrades on two devices
- making sure deletion removes both auth identity and linked gameplay identity

## Definition of done

- account creation and login happen through Supabase Auth
- existing profile stats survive migration
- guest upgrade works
- password reset works
- email verification works
- app code no longer stores or verifies password hashes directly
