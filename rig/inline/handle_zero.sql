-- Inline rig: restart SGSC's handle position (En ligne tab) from zero, now. For
-- when the handle has been moved without a confirmation: a cleaning, a re-set by
-- hand. Only that display reads it; Caliban ignores this setting.

update system_config
set value = (value::jsonb || jsonb_build_object('handle_zero_at', now()))::text
where key = 'inline_settings';

select value::jsonb ->> 'handle_zero_at' as handle_zero_at from system_config where key = 'inline_settings';
