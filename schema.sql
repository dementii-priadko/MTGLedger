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

-- Each side can clear settled (returned) records from their own view; a row both sides cleared is deleted.
ALTER TABLE loans ADD COLUMN IF NOT EXISTS hidden_by_lender BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS hidden_by_borrower BOOLEAN NOT NULL DEFAULT false;

-- The Missing board: cards someone lent out but lost track of, visible to every user. Whoever turns out
-- to have one claims it, which turns it into a loan.
CREATE TABLE IF NOT EXISTS missing_cards (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id    BIGINT NOT NULL REFERENCES cards(id),
    owner_id   BIGINT NOT NULL REFERENCES users(id),
    quantity   INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, card_id)
);

-- Who has traded with whom: one row per direction, added whenever a loan is recorded and kept after the
-- loans are cleared. Decides whose Missing cards you see; `hidden` = you chose to stop seeing that person's.
CREATE TABLE IF NOT EXISTS contacts (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contact_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hidden     BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, contact_id),
    CHECK (user_id <> contact_id)
);
-- Backfill from the ledger (a no-op once everything is in).
INSERT INTO contacts (user_id, contact_id)
SELECT lender_id, borrower_id FROM loans UNION SELECT borrower_id, lender_id FROM loans
ON CONFLICT DO NOTHING;

-- "Looking for": cards someone would like to borrow, shown to their contacts like the Missing board.
-- any_printing: any version will do (card_id is then just a representative printing for the name and
-- picture); the lender picks the exact printing when offering.
CREATE TABLE IF NOT EXISTS wanted_cards (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id      BIGINT NOT NULL REFERENCES cards(id),
    owner_id     BIGINT NOT NULL REFERENCES users(id),
    quantity     INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    any_printing BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, card_id, any_printing)
);

-- A contact offering to lend a wanted card. Nothing reaches the ledger until the requester accepts.
CREATE TABLE IF NOT EXISTS wanted_offers (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wanted_id  BIGINT NOT NULL REFERENCES wanted_cards(id) ON DELETE CASCADE,
    lender_id  BIGINT NOT NULL REFERENCES users(id),
    card_id    BIGINT REFERENCES cards(id),          -- the printing offered for an any-version request
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (wanted_id, lender_id)
);
