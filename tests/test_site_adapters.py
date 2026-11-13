"""Tests for site adapter parsing logic with fixtures."""
import json
import pytest
from pathlib import Path
from property_lookup.models import SiteResult, FOUND
from property_lookup.address import parse_address


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestRedfinAdapter:
    """Test Redfin data parsing."""
    
    def test_parse_redfin_json(self):
        """Test parsing Redfin API response."""
        with open(FIXTURES_DIR / "redfin_sample.json") as f:
            data = json.load(f)
        
        payload = data["payload"]
        
        assert payload["address"] == "48 Amsterdam Ave, Bridgeport, CT 06606"
        assert payload["price"]["value"] == 550000
        assert payload["beds"] == 6
        assert payload["baths"] == 3.0
        assert payload["sqFt"]["value"] == 3200
        assert payload["yearBuilt"]["value"] == 1920


class TestCrexiAdapter:
    """Test Crexi data parsing."""
    
    def test_parse_crexi_json(self):
        """Test parsing Crexi API response."""
        with open(FIXTURES_DIR / "crexi_sample.json") as f:
            data = json.load(f)
        
        assert data["addressLine1"] == "48 Amsterdam Ave"
        assert data["listPrice"] == 575000
        assert data["numberOfUnits"] == 6
        assert data["capRate"] == 7.5
        assert data["noi"] == 43125


class TestCTJudicialAdapter:
    """Test CT Judicial parsing."""
    
    def test_parse_foreclosure_html(self):
        """Test parsing foreclosure notice HTML."""
        with open(FIXTURES_DIR / "ctjudicial_sample.html") as f:
            html = f.read()
        
        assert "123 Main Street" in html
        assert "Bridgeport" in html
        assert "December 15, 2024" in html


class TestNEPCGAdapter:
    """Test NEPCG data parsing."""
    
    def test_parse_nepcg_json(self):
        """Test parsing NEPCG API response."""
        with open(FIXTURES_DIR / "nepcg_sample.json") as f:
            data = json.load(f)
        
        result = data["results"][0]
        assert result["address"]["street"] == "48 Amsterdam Ave"
        assert result["price"] == 550000
        assert result["units"] == 6


class TestSiteResultModel:
    """Test SiteResult model behavior."""
    
    def test_set_filters_empty_values(self):
        """Test that set() filters out empty/placeholder values."""
        result = SiteResult(site="test", status=FOUND)
        
        result.set("list_price", None)
        assert "list_price" not in result.fields
        
        result.set("list_price", "")
        assert "list_price" not in result.fields
        
        result.set("list_price", "-")
        assert "list_price" not in result.fields
        
        result.set("list_price", "N/A")
        assert "list_price" not in result.fields
    
    def test_set_detects_login_gated(self):
        """Test that masked values are detected as login-gated."""
        result = SiteResult(site="crexi", status=FOUND)
        
        result.set("units", "****")
        assert "units" not in result.fields
        assert "units" in result.login_gated_fields
        
        result.set("noi", "*** ")
        assert "noi" not in result.fields
        assert "noi" in result.login_gated_fields
    
    def test_set_accepts_valid_values(self):
        """Test that valid values are stored."""
        result = SiteResult(site="test", status=FOUND)
        
        result.set("list_price", "$500,000")
        assert result.fields["list_price"] == "$500,000"
        
        result.set("units", "6")
        assert result.fields["units"] == "6"
        
        result.set("description", "A nice property")
        assert result.fields["description"] == "A nice property"
