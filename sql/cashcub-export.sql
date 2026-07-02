select b.budget_year as year, 
    b.budget_month as month, 
    c."name" as category,
    bi."name" as item, 
    t."date", 
    t.amount, 
    t.merchant, 
    t.notes from budgets b
    inner join transactions t 
        on b.id = t.budget_id
    inner join budget_items bi 
        on bi.id = t.budget_item_id 
    inner join categories c 
    on bi.category_id = c.id
order by "date";