# Outbound messages

!!! note "Placeholder"
    This page hasn't been written yet. It will cover the following.

What CareFlow sends to hospital systems: signed JSON webhooks per system and HL7 ADT^A03 to the HIS when a discharge closes, with retries and logging.

!!! warning "Not built yet"
    Outbound retries are in memory (3 attempts). There's no persistent outbound queue or dead-letter queue, so a restart during a retry loses that attempt; it stays in the message log as failed.
