from app.analyzer.comparison import calculate_code_churn

def test_code_churn_modified_files_use_loc_delta():
    before = [
        {"file_path": "a.py", "loc_total": 20},
        {"file_path": "old.py", "loc_total": 5},
    ]
    after = [
        {"file_path": "a.py", "loc_total": 25},
        {"file_path": "new.py", "loc_total": 2},
    ]
    result = calculate_code_churn(before, after)
    assert result["available"] is True
    assert result["modified_files"] == 1
    assert result["added_files"] == 1
    assert result["removed_files"] == 1
    assert result["modified_loc"] == 5
    assert result["churned_loc"] == 12
    assert result["score"] > 0.0
