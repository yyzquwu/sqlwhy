SELECT o.id, p.status
FROM orders AS o
LEFT JOIN payments AS p ON o.id = p.order_id
WHERE p.status = 'settled'
