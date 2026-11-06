from pathlib import Path
import pytest
from openpyxl import load_workbook
from app.schemas import WorkbookDoc
from renderers.xlsx_renderer import render_xlsx
from validators.xlsx_validator import validate_xlsx
from validators.content_validator import validate_metric_claims


def sample():
    return WorkbookDoc.model_validate({
        'title':'Sales','sheets':[{'name':'Sales','columns':['Month','Revenue','Cost'],
        'rows':[['Jan',100,60],['Feb',150,80]],'totals_row':True,
        'computed_columns':[{'name':'Profit','formula_type':'difference','columns':['Revenue','Cost']}]}],
        'charts':[{'kind':'bar','title':'Revenue','sheet':'Sales','category_column':'Month','value_columns':['Revenue']}],
        'include_dashboard':True})


def test_good_workbook(tmp_path):
    doc=sample(); path=render_xlsx(doc,tmp_path/'sales.xlsx')
    assert validate_xlsx(path,doc).status == 'completed'


def test_modified_data_fails(tmp_path):
    doc=sample(); path=render_xlsx(doc,tmp_path/'sales.xlsx')
    wb=load_workbook(path);wb['Sales']['B2']=999;wb.save(path)
    assert 'data_mismatch' in [i.code for i in validate_xlsx(path,doc).issues]


def test_chart_type_fails(tmp_path):
    from openpyxl.chart import LineChart
    doc=sample(); path=render_xlsx(doc,tmp_path/'sales.xlsx')
    wb=load_workbook(path)
    chart=wb['Dashboard']._charts[0]
    other=LineChart(); other.title='Revenue';other.series=chart.series
    wb['Dashboard']._charts=[other];wb.save(path)
    assert 'chart_type' in [i.code for i in validate_xlsx(path,doc).issues]


def test_content_claim_wrong():
    result=validate_metric_claims([{'metric_id':'revenue','value':250,'text':'Revenue is 250'}],{'revenue':100})
    assert 'incorrect_claim' in [i.code for i in result.issues]


def test_content_claim_text_not_certified():
    result=validate_metric_claims([{'metric_id':'revenue','value':100,'text':'Revenue is actually 999'}],{'revenue':100})
    assert 'prose_unverified' in [i.code for i in result.issues]
