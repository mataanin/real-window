"""Tests for merge and conflict detection logic."""
import pytest
from property_lookup.merge import merge, _norm, _same, _first_num, _lot_sf
from property_lookup.models import SiteResult, FOUND, NOT_FOUND


class TestNumberExtraction:
    """Test number extraction utilities."""
    
    def test_first_num_basic(self):
        """Test basic number extraction."""
        assert _first_num("$500,000") == 500000
        assert _first_num("1,234.56") == 1234.56
        assert _first_num("Price: $1,500") == 1500
    
    def test_first_num_negative(self):
        """Test negative number extraction."""
        assert _first_num("-123.45") == -123.45
    
    def test_first_num_none(self):
        """Test no number found."""
        assert _first_num("no numbers") is None
        assert _first_num("") is None
    
    def test_lot_sf_acres(self):
        """Test lot size conversion from acres."""
        assert _lot_sf("0.5 acres") == pytest.approx(21780, rel=0.01)
        assert _lot_sf("1.25 ac") == pytest.approx(54450, rel=0.01)
    
    def test_lot_sf_square_feet(self):
        """Test lot size in square feet."""
        assert _lot_sf("5000 sq ft") == 5000
        assert _lot_sf("12,632") == 12632


class TestNormalization:
    """Test value normalization."""
    
    def test_norm_date(self):
        """Test date normalization."""
        typ, val = _norm("list_date", "12/25/2023")
        assert typ == "txt"
        assert val == "2023-12-25"
    
    def test_norm_numeric_field(self):
        """Test numeric field normalization."""
        typ, val = _norm("list_price", "$500,000")
        assert typ == "num"
        assert val == 500000
    
    def test_norm_status(self):
        """Test status normalization."""
        typ, val = _norm("status", "For Sale")
        assert typ == "txt"
        assert val == "active"
        
        typ, val = _norm("status", "Under Contract")
        assert typ == "txt"
        assert val == "pending"
    
    def test_norm_text(self):
        """Test text field normalization."""
        typ, val = _norm("broker", "ABC Realty")
        assert typ == "txt"
        assert "abc realty" in val.lower()


class TestSameComparison:
    """Test value equivalence checking."""
    
    def test_same_exact_numbers(self):
        """Test exact numeric match."""
        assert _same("units", ("num", 6), ("num", 6))
    
    def test_same_price_tolerance(self):
        """Test price comparison with tolerance."""
        assert _same("list_price", ("num", 500000), ("num", 500000))
        assert _same("list_price", ("num", 500000), ("num", 504000))
        assert not _same("list_price", ("num", 500000), ("num", 550000))
    
    def test_same_lot_size_tolerance(self):
        """Test lot size comparison with tolerance."""
        # 0.29 acres ~= 12,632 sf, should match within tolerance
        assert _same("lot_size", ("num", 12632), ("num", 12700))
    
    def test_same_text(self):
        """Test text comparison."""
        assert _same("broker", ("txt", "abc realty"), ("txt", "abc realty"))
        assert not _same("broker", ("txt", "abc realty"), ("txt", "xyz realty"))


class TestMerge:
    """Test merging results from multiple sites."""
    
    def test_merge_single_source(self):
        """Test merge with single source."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"list_price": "$500,000", "units": "6"}
            )
        ]
        merged, conflicts = merge(results)
        
        assert "list_price" in merged
        assert len(merged["list_price"]) == 1
        assert merged["list_price"][0]["sources"] == ["zillow"]
        assert conflicts == []
    
    def test_merge_agreeing_sources(self):
        """Test merge when sources agree."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"list_price": "$500,000", "units": "6"}
            ),
            SiteResult(
                site="redfin",
                status=FOUND,
                fields={"list_price": "$500,000", "units": "6"}
            )
        ]
        merged, conflicts = merge(results)
        
        assert len(merged["list_price"]) == 1
        assert set(merged["list_price"][0]["sources"]) == {"zillow", "redfin"}
        assert conflicts == []
    
    def test_merge_with_conflicts(self):
        """Test merge with conflicting values."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"units": "6", "year_built": "1920"}
            ),
            SiteResult(
                site="redfin",
                status=FOUND,
                fields={"units": "8", "year_built": "1920"}
            )
        ]
        merged, conflicts = merge(results)
        
        assert len(merged["units"]) == 2
        assert len(conflicts) > 0
        
        conflict_fields = [c["field"] for c in conflicts]
        assert "units" in conflict_fields
        assert "year_built" not in conflict_fields
    
    def test_merge_price_tolerance(self):
        """Test merge with slightly different prices (within tolerance)."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"list_price": "$500,000"}
            ),
            SiteResult(
                site="redfin",
                status=FOUND,
                fields={"list_price": "$502,000"}
            )
        ]
        merged, conflicts = merge(results)
        
        assert len(merged["list_price"]) == 1
        assert conflicts == []
    
    def test_merge_skip_not_found(self):
        """Test merge skips results with not_found status."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"list_price": "$500,000"}
            ),
            SiteResult(
                site="redfin",
                status=NOT_FOUND
            )
        ]
        merged, conflicts = merge(results)
        
        assert len(merged["list_price"]) == 1
        assert merged["list_price"][0]["sources"] == ["zillow"]
    
    def test_merge_no_conflict_fields(self):
        """Test that NO_CONFLICT fields don't appear in conflicts."""
        results = [
            SiteResult(
                site="zillow",
                status=FOUND,
                fields={"broker": "ABC Realty", "photo_count": "10"}
            ),
            SiteResult(
                site="redfin",
                status=FOUND,
                fields={"broker": "XYZ Realty", "photo_count": "15"}
            )
        ]
        merged, conflicts = merge(results)
        
        conflict_fields = [c["field"] for c in conflicts]
        assert "broker" not in conflict_fields
        assert "photo_count" not in conflict_fields
    
    def test_merge_ordering(self):
        """Test that most common values come first."""
        results = [
            SiteResult(site="site1", status=FOUND, fields={"units": "6"}),
            SiteResult(site="site2", status=FOUND, fields={"units": "6"}),
            SiteResult(site="site3", status=FOUND, fields={"units": "6"}),
            SiteResult(site="site4", status=FOUND, fields={"units": "8"}),
        ]
        merged, conflicts = merge(results)
        
        assert len(merged["units"]) == 2
        assert len(merged["units"][0]["sources"]) == 3
        assert len(merged["units"][1]["sources"]) == 1
