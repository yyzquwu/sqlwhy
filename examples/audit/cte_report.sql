WITH orders_with_payments AS (
    SELECT o.id AS order_id, p.status FROM orders AS o
    LEFT JOIN payments AS p ON p.id = o.primary_payment_id
)
SELECT order_id, status FROM orders_with_payments
