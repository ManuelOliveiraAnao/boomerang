CREATE TABLE IF NOT EXISTS "orders" (
  "order_id" TEXT NOT NULL,
  "customer_id" TEXT,
  data JSONB NOT NULL,
  PRIMARY KEY ("order_id")
);
CREATE INDEX IF NOT EXISTS "customer_id_index" ON "orders" ("customer_id");
