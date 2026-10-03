-- People. A row with telegram_id NULL is a placeholder created when someone
-- records a loan with an @username who hasn't logged in yet; it gets claimed
-- on that person's first Telegram login.
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    photo_url   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Telegram usernames are case-insensitive.
CREATE UNIQUE INDEX IF NOT EXISTS users_username_key ON users (lower(username));

-- A specific physical card variant: one printing (edition) of a card in one finish.
CREATE TABLE IF NOT EXISTS cards (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game             TEXT NOT NULL DEFAULT 'mtg',
    external_id      TEXT NOT NULL,              -- Scryfall printing id for mtg
    name             TEXT NOT NULL,
    set_code         TEXT,
    set_name         TEXT,
    collector_number TEXT,
    image_url        TEXT,
    finish           TEXT NOT NULL DEFAULT 'nonfoil'
                     CHECK (finish IN ('nonfoil', 'foil', 'etched')),
    UNIQUE (game, external_id, finish)
);

-- The ledger: card <-> (lender, borrower).
CREATE TABLE IF NOT EXISTS loans (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id     BIGINT NOT NULL REFERENCES cards(id),
    lender_id   BIGINT NOT NULL REFERENCES users(id),
    borrower_id BIGINT NOT NULL REFERENCES users(id),
    quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    status      TEXT NOT NULL DEFAULT 'borrowed' CHECK (status IN ('borrowed', 'returned')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    returned_at TIMESTAMPTZ,
    created_by  BIGINT REFERENCES users(id),
    CHECK (lender_id <> borrower_id)
);

CREATE INDEX IF NOT EXISTS loans_lender   ON loans (lender_id, status);
CREATE INDEX IF NOT EXISTS loans_borrower ON loans (borrower_id, status);

-- Cards recorded together as one loan share a batch id (added after the initial schema).
ALTER TABLE loans ADD COLUMN IF NOT EXISTS batch_id UUID;
CREATE INDEX IF NOT EXISTS loans_batch ON loans (batch_id);
