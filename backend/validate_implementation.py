#!/usr/bin/env python
"""
Comprehensive implementation validation script.
Tests models, CRUD, routes, database, and migrations.
"""
import sys
import os

# Add backend to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

def test_step(name, func):
    """Run a test step and report results."""
    print(f"\n{'='*70}")
    print(f"[TEST] {name}")
    print('='*70)
    try:
        func()
        print("✅ PASSED")
        return True
    except AssertionError as e:
        print(f"❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_imports():
    """Test 1: Verify all model imports."""
    print("Importing models...")
    from app.models import (
        UserAccount, SubletRequest, Party, Listing, 
        Occupancy, Application, IdentityVerification
    )
    print("  ✓ UserAccount")
    print("  ✓ SubletRequest")
    print("  ✓ Party")
    print("  ✓ Listing")
    print("  ✓ Occupancy")
    
    print("\nImporting CRUD modules...")
    from app.crud import user, sublet
    print("  ✓ app.crud.user")
    print("  ✓ app.crud.sublet")
    
    print("\nImporting routes...")
    from app.api.routes import user_auth, user_identity, user_rentals, user_hosting
    print("  ✓ user_auth routes")
    print("  ✓ user_identity routes")
    print("  ✓ user_rentals routes")
    print("  ✓ user_hosting routes")
    
    print("\nImporting FastAPI app...")
    from app.main import app
    print(f"  ✓ FastAPI app created with {len(app.routes)} routes")

def test_app_routes():
    """Test 2: Verify new routes are registered."""
    print("Checking FastAPI routes...")
    from app.main import app
    
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    required_routes = [
        '/api/users/register',
        '/api/users/login',
        '/api/users/me',
        '/api/users/identity-verifications',
        '/api/users/rentals/applications',
        '/api/users/rentals/occupancies',
        '/api/users/hosting/properties',
    ]
    
    found = []
    missing = []
    for route in required_routes:
        if any(route in r for r in routes):
            found.append(route)
        else:
            missing.append(route)
    
    print(f"\n✓ Found routes ({len(found)}):")
    for r in found:
        print(f"  - {r}")
    
    if missing:
        print(f"\n⚠ Missing routes ({len(missing)}):")
        for r in missing:
            print(f"  - {r}")
    
    assert len(missing) == 0, f"Missing {len(missing)} required routes"

def test_database_connection():
    """Test 3: Verify database connection."""
    print("Connecting to database...")
    from app.core.config import settings
    from sqlalchemy import create_engine, text, inspect
    
    print(f"  Database URL: {settings.database_url[:50]}...")
    
    try:
        engine = create_engine(settings.database_url)
        conn = engine.connect()
        
        # Check alembic version
        try:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            if version:
                print(f"  ✓ Current migration: {version[0]}")
            else:
                print(f"  ⚠ No migration version found (migrations not initialized)")
        except Exception as e:
            print(f"  ⚠ Alembic check failed: {type(e).__name__}")
        
        # Check for required tables
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        
        required_tables = ['user_accounts', 'sublet_requests', 'parties', 'listings']
        
        print(f"\n  Total tables: {len(tables)}")
        print("\n  Required tables:")
        for tbl in required_tables:
            if tbl in tables:
                print(f"    ✓ {tbl}")
            else:
                print(f"    ✗ {tbl} NOT FOUND")
        
        # Check for new columns
        if 'listings' in tables:
            cols = {col['name'] for col in inspector.get_columns('listings')}
            if 'party_id' in cols:
                print(f"\n  ✓ listings.party_id column exists")
            else:
                print(f"\n  ⚠ listings.party_id column NOT FOUND")
        
        if 'user_accounts' in tables:
            cols = {col['name'] for col in inspector.get_columns('user_accounts')}
            required_cols = {'id', 'email', 'hashed_password', 'party_id', 'is_active', 'email_verified'}
            missing_cols = required_cols - cols
            if missing_cols:
                print(f"\n  ⚠ Missing columns in user_accounts: {missing_cols}")
            else:
                print(f"\n  ✓ user_accounts has all required columns")
        
        conn.close()
        
    except Exception as e:
        print(f"  ✗ Database connection failed: {e}")
        raise

def test_schemas():
    """Test 4: Verify Pydantic schemas."""
    print("Testing schemas...")
    from app.schemas.user import (
        UserLoginRequest, UserRegisterRequest, UserRead,
        UserPasswordChangeRequest, UserProfileUpdateRequest
    )
    
    # Test UserLoginRequest
    login = UserLoginRequest(email="test@example.com", password="pass123")
    print(f"  ✓ UserLoginRequest: {login.model_dump()}")
    
    # Test UserRegisterRequest
    register = UserRegisterRequest(
        email="new@example.com",
        password="pass123",
        full_name="John Doe",
        phone="+1234567890"
    )
    print(f"  ✓ UserRegisterRequest validates")
    
    print(f"  ✓ UserRead, PasswordChange, ProfileUpdate schemas load")

def test_crud_functions():
    """Test 5: Verify CRUD function signatures."""
    print("Checking CRUD functions...")
    from app.crud import user
    
    required_funcs = [
        'create_user',
        'get_user_by_email',
        'authenticate_user',
        'update_user_password',
        'update_user_profile',
    ]
    
    for func_name in required_funcs:
        if hasattr(user, func_name):
            print(f"  ✓ {func_name}")
        else:
            raise AssertionError(f"Missing function: {func_name}")
    
    from app.crud import sublet
    sublet_funcs = [
        'submit_sublet_request',
        'approve_sublet_request',
        'reject_sublet_request',
        'list_pending_sublet_requests',
    ]
    
    for func_name in sublet_funcs:
        if hasattr(sublet, func_name):
            print(f"  ✓ {func_name}")
        else:
            raise AssertionError(f"Missing function: {func_name}")

def test_dependencies():
    """Test 6: Verify authentication dependencies."""
    print("Testing authentication dependencies...")
    from app.api.deps import get_current_user, get_current_admin, get_db
    
    print(f"  ✓ get_current_user exists")
    print(f"  ✓ get_current_admin exists")
    print(f"  ✓ get_db exists")

def main():
    """Run all tests."""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*20 + "ZOIKO IMPLEMENTATION VALIDATION" + " "*28 + "║")
    print("╚" + "="*78 + "╝")
    
    tests = [
        ("Imports & Modules", test_imports),
        ("FastAPI Routes", test_app_routes),
        ("Database Connection", test_database_connection),
        ("Pydantic Schemas", test_schemas),
        ("CRUD Functions", test_crud_functions),
        ("Authentication Dependencies", test_dependencies),
    ]
    
    results = []
    for name, func in tests:
        results.append(test_step(name, func))
    
    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print('='*70)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nTests Passed: {passed}/{total}")
    
    for i, (name, _) in enumerate(tests):
        status = "✅" if results[i] else "❌"
        print(f"  {status} {name}")
    
    print("\n" + "="*70)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("\nNext steps:")
        print("  1. Ensure database migrations are applied: alembic upgrade head")
        print("  2. Start the development server: python -m uvicorn app.main:app --reload")
        print("  3. Test endpoints with curl/Postman")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        print("\nPlease review the errors above and fix any issues.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
