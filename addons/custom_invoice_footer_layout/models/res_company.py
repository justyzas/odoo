from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    invoice_footer_col_1 = fields.Html(string="Invoice Footer Column 1", sanitize=False)
    invoice_footer_col_2 = fields.Html(string="Invoice Footer Column 2", sanitize=False)
    invoice_footer_col_3 = fields.Html(string="Invoice Footer Column 3", sanitize=False)
