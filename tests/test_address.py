"""Tests for address parsing and normalization."""
import pytest
from property_lookup.address import (
    parse_address, norm_street, street_numbers, match_score, is_match, slugify
)


class TestAddressParsing:
    """Test address parsing."""
    
    def test_parse_full_address(self):
        """Test parsing a complete address."""
        addr = parse_address("48 Amsterdam Ave, Bridgeport, CT 06606")
        assert addr.number == "48"
        assert addr.street == "Amsterdam Ave"
        assert addr.city == "Bridgeport"
        assert addr.state == "CT"
        assert addr.zip == "06606"
    
    def test_parse_with_unit(self):
        """Test parsing address with unit number."""
        addr = parse_address("800 Seaview Ave #770-5, Bridgeport, CT")
        assert addr.number == "800"
        assert addr.street == "Seaview Ave"
        assert addr.unit == "770-5"
        assert addr.city == "Bridgeport"
    
    def test_parse_range_address(self):
        """Test parsing address with number range."""
        addr = parse_address("613-615 Main St, New Haven, CT 06511")
        assert addr.number == "613-615"
        assert addr.street == "Main St"
        assert addr.city == "New Haven"
    
    def test_parse_no_commas(self):
        """Test parsing address without commas."""
        addr = parse_address("18 Maple St Trumbull CT 06611")
        assert addr.number == "18"
        assert addr.street == "Maple St"
        assert addr.city == "Trumbull"
        assert addr.state == "CT"
    
    def test_one_line_property(self):
        """Test one_line property."""
        addr = parse_address("48 Amsterdam Ave, Bridgeport, CT 06606")
        assert addr.one_line == "48 Amsterdam Ave, Bridgeport, CT 06606"
    
    def test_slug_property(self):
        """Test slug generation."""
        addr = parse_address("48 Amsterdam Ave, Bridgeport, CT 06606")
        assert addr.slug == "48-amsterdam-ave-bridgeport-ct-06606"


class TestStreetNormalization:
    """Test street name normalization."""
    
    def test_norm_street_basic(self):
        """Test basic street normalization."""
        assert norm_street("48 Amsterdam Avenue") == "amsterdam ave"
        assert norm_street("100 Main Street") == "main st"
        assert norm_street("55 Oak Drive") == "oak dr"
    
    def test_norm_street_direction(self):
        """Test street with direction."""
        assert norm_street("North Main Street") == "n main st"
        assert norm_street("Main Street South") == "main street s"
    
    def test_norm_street_multiple_suffixes(self):
        """Test handling of suffix variations."""
        assert norm_street("Boulevard") == "blvd"
        assert norm_street("Parkway") == "pkwy"
        assert norm_street("Circle") == "cir"


class TestStreetNumbers:
    """Test street number extraction."""
    
    def test_single_number(self):
        """Test single street number."""
        assert street_numbers("613 Main St") == {613}
    
    def test_range(self):
        """Test number range."""
        nums = street_numbers("613-615 Main St")
        assert 613 in nums
        assert 614 in nums
        assert 615 in nums
    
    def test_multiple_numbers(self):
        """Test multiple numbers with &."""
        nums = street_numbers("53 & 59 Main St")
        assert 53 in nums
        assert 59 in nums
    
    def test_abbreviated_range(self):
        """Test abbreviated range like 613-15."""
        nums = street_numbers("613-15 Main St")
        assert 613 in nums
        assert 615 in nums


class TestAddressMatching:
    """Test address matching and scoring."""
    
    def test_exact_match(self):
        """Test exact address match."""
        target = parse_address("613 Main St, New Haven, CT")
        assert match_score(target, "613 Main Street") > 90
        assert is_match(target, "613 Main Street")
    
    def test_different_suffix(self):
        """Test matching with different suffix abbreviation."""
        target = parse_address("100 Oak Drive, Bridgeport, CT")
        score = match_score(target, "100 Oak Dr")
        assert score > 85
    
    def test_wrong_number(self):
        """Test non-match with wrong number."""
        target = parse_address("613 Main St, New Haven, CT")
        assert match_score(target, "615 Main Street") == 0
    
    def test_range_match(self):
        """Test matching within a range."""
        target = parse_address("614 Main St, New Haven, CT")
        assert is_match(target, "613-615 Main Street")
    
    def test_wrong_city_penalty(self):
        """Test city mismatch penalty."""
        target = parse_address("100 Main St, Bridgeport, CT")
        score_wrong_city = match_score(target, "100 Main St", cand_city="New Haven")
        score_right_city = match_score(target, "100 Main St", cand_city="Bridgeport")
        assert score_right_city > score_wrong_city


class TestSlugify:
    """Test slug generation."""
    
    def test_slugify_basic(self):
        """Test basic slugification."""
        assert slugify("Hello World") == "hello-world"
        assert slugify("Main Street, CT") == "main-street-ct"
    
    def test_slugify_special_chars(self):
        """Test slugification with special characters."""
        assert slugify("613-615 Main St #5") == "613-615-main-st-5"
        assert slugify("Apt. 3B @ Building") == "apt-3b-building"
