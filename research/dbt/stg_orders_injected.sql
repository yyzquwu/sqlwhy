-- Deliberate modification of dbt Labs' Apache-2.0 stg_orders compilation.
-- sqlwhy fixture: the WHERE clause in renamed incorrectly excludes non-completed orders.
with source as (
    select * from "evidence"."main"."raw_orders"

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        order_date,
        status

    from source
    where status = 'completed'

)

select * from renamed
