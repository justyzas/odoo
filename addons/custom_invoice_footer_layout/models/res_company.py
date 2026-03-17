from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    invoice_footer_col_1 = fields.Text(string="Invoice Footer Column 1")
    invoice_footer_col_2 = fields.Text(string="Invoice Footer Column 2")
    invoice_footer_col_3 = fields.Text(string="Invoice Footer Column 3")
