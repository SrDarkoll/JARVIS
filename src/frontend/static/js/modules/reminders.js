/** Pure reminder presentation helpers. */

export function reminderNotificationText(reminder, translate) {
    const id = String(reminder?.id || '').trim();
    const text = String(reminder?.text || '').replace(/\s+/g, ' ').trim();
    if (!id || !text) return '';
    const template = typeof translate === 'function'
        ? translate('reminder_due')
        : 'Reminder: {text}';
    return String(template || 'Reminder: {text}').replace('{text}', text);
}
