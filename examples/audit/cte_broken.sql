WITH orders_with_payments AS (
    SELECT o.id AS order_id, p.status FROM orders AS o
    LEFT JOIN payments AS p ON p.order_id = o.id
), settled_orders AS (
    SELECT order_id FROM orders_with_payments WHERE status = 'settled'
)
SELECT order_id FROM settled_orders
