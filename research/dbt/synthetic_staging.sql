-- Original synthetic inputs mapped to the relations emitted by dbt compile.
CREATE SCHEMA main_quickbooks_staging;
SET schema = 'main_quickbooks_staging';
CREATE TABLE stg_quickbooks__invoice (
    invoice_id INTEGER, source_relation VARCHAR, doc_number VARCHAR, department_id INTEGER,
    customer_id INTEGER, billing_address_id INTEGER, shipping_address_id INTEGER,
    delivery_type VARCHAR, total_amount INTEGER, balance INTEGER, due_date DATE
);
INSERT INTO stg_quickbooks__invoice VALUES
    (10, 'alpha', '101', 1, 1, 4, 5, 'email', 100, 70, DATE '2024-01-10'),
    (10, 'beta', '102', 2, 2, 8, 9, 'email', 100, 80, DATE '2024-01-10');
CREATE TABLE stg_quickbooks__invoice_linked_txn (invoice_id INTEGER, source_relation VARCHAR, estimate_id INTEGER, payment_id INTEGER);
INSERT INTO stg_quickbooks__invoice_linked_txn VALUES (10, 'alpha', NULL, 100), (10, 'beta', NULL, 200);
CREATE TABLE stg_quickbooks__estimate (estimate_id INTEGER, source_relation VARCHAR, total_amount INTEGER, transaction_status VARCHAR);
CREATE TABLE stg_quickbooks__payment (payment_id INTEGER, source_relation VARCHAR, transaction_date DATE);
INSERT INTO stg_quickbooks__payment VALUES (100, 'alpha', DATE '2024-01-01'), (200, 'beta', DATE '2024-01-02');
CREATE TABLE stg_quickbooks__payment_line (payment_id INTEGER, invoice_id INTEGER, source_relation VARCHAR, amount INTEGER);
INSERT INTO stg_quickbooks__payment_line VALUES (100, 10, 'alpha', 30), (200, 10, 'beta', 20);

CREATE TABLE stg_quickbooks__bill_payment (
    bill_payment_id INTEGER, source_relation VARCHAR, transaction_date DATE, total_amount INTEGER,
    credit_card_account_id INTEGER, check_bank_account_id INTEGER, vendor_id INTEGER, department_id INTEGER
);
INSERT INTO stg_quickbooks__bill_payment VALUES
    (1, 'alpha', DATE '2024-01-01', 100, NULL, 601, 1, 1),
    (2, 'beta', DATE '2024-01-02', 100, NULL, 602, 2, 2);
CREATE TABLE stg_quickbooks__bill_payment_line (bill_payment_id INTEGER);
CREATE TABLE stg_quickbooks__account (account_id INTEGER, source_relation VARCHAR, account_type VARCHAR, is_active BOOLEAN, is_sub_account BOOLEAN);
INSERT INTO stg_quickbooks__account VALUES (900, 'beta', 'Accounts Payable', true, false);

CREATE SCHEMA main_stg_shopify;
SET schema = 'main_stg_shopify';
CREATE TABLE stg_shopify__abandoned_checkout (checkout_id INTEGER, source_relation VARCHAR, is_deleted BOOLEAN, total_line_items_price INTEGER);
INSERT INTO stg_shopify__abandoned_checkout VALUES (10, 'alpha', false, 40);
CREATE TABLE stg_shopify__abandoned_checkout_discount_code (checkout_id INTEGER, source_relation VARCHAR, code VARCHAR, type VARCHAR, amount INTEGER);
INSERT INTO stg_shopify__abandoned_checkout_discount_code VALUES (10, 'alpha', 'save5', 'fixed_amount', 5);
CREATE TABLE stg_shopify__abandoned_checkout_shipping_line (checkout_id INTEGER, source_relation VARCHAR, price INTEGER);
INSERT INTO stg_shopify__abandoned_checkout_shipping_line VALUES (10, 'alpha', 2), (10, 'alpha', 3);

CREATE SCHEMA main_zendesk_source;
CREATE TABLE main_zendesk_source.stg_zendesk__ticket (source_relation VARCHAR, ticket_id INTEGER, created_at TIMESTAMP);
INSERT INTO main_zendesk_source.stg_zendesk__ticket VALUES
    ('alpha', 1, TIMESTAMP '2024-01-01 12:00'), ('alpha', 2, TIMESTAMP '2024-01-01 12:00');
CREATE SCHEMA main_zendesk_intermediate;
SET schema = 'main_zendesk_intermediate';
CREATE TABLE int_zendesk__ticket_historical_status (source_relation VARCHAR, ticket_id INTEGER, status VARCHAR, valid_starting_at TIMESTAMP);
INSERT INTO int_zendesk__ticket_historical_status VALUES
    ('alpha', 1, 'closed', TIMESTAMP '2024-01-01 12:10'), ('alpha', 2, 'solved', TIMESTAMP '2024-01-01 12:15');
CREATE TABLE int_zendesk__ticket_historical_assignee (
    source_relation VARCHAR, ticket_id INTEGER, unique_assignee_count INTEGER, assignee_stations_count INTEGER,
    first_assignee_id INTEGER, last_assignee_id INTEGER, first_agent_assignment_date TIMESTAMP,
    last_agent_assignment_date TIMESTAMP, ticket_unassigned_duration_calendar_minutes INTEGER
);
CREATE TABLE int_zendesk__ticket_historical_group (source_relation VARCHAR, ticket_id INTEGER, group_stations_count INTEGER);
SET schema = 'main';
