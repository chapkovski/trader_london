from fastapi.testclient import TestClient
from httpx import AsyncClient
import pytest
from main import app
import logging

logging.basicConfig(level=logging.DEBUG)
from httpx import AsyncClient
import pytest
from main import app
import logging

logging.basicConfig(level=logging.DEBUG)


@pytest.mark.asyncio
async def test_workflow():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Step 1: Create a new trading session
        session_response = await ac.post("/create_session/")
        assert session_response.status_code == 200
        logging.info(f'Session response: {session_response.json()}')
        session_data = session_response.json()
        session_id = session_data.get("session_id")
        assert session_id is not None

        # Step 2: Create a new trader 1 and 2
        trader_response_1 = await ac.post("/create_trader/", json={"cash": 1000.0, "stocks": 100})
        assert trader_response_1.status_code == 200
        trader_data_1 = trader_response_1.json()
        trader_id_1 = trader_data_1.get("trader_id")
        assert trader_id_1 is not None

        trader_response_2 = await ac.post("/create_trader/", json={"cash": 1000.0, "stocks": 100})
        assert trader_response_2.status_code == 200
        trader_data_2 = trader_response_2.json()
        trader_id_2 = trader_data_2.get("trader_id")
        assert trader_id_2 is not None


        # Step 3: Connect the traders  to the session
        traders = [trader_id_1, trader_id_2]
        for trader in traders:
            connect_response = await ac.post("/connect_trader/", json={"trader_id": trader, "session_id": session_id})
            assert connect_response.status_code == 200
            logging.info(f'Connect response: {connect_response.json()}')



        # Fetch the list of traders
        traders_response = await ac.get("/get_traders/")
        assert traders_response.status_code == 200
        traders_data = traders_response.json()
        logging.info(f'Traders response: {traders_data}')

        # Step 4: Place orders
        # Trader1 places a bid order
        order_response_1 = await ac.post("/place_order/", json={
            "trader_id": trader_id_1,
            "session_id": session_id,
            "order_type": "bid",
            "quantity": 1,
            "price": 50.0
        })

        assert order_response_1.status_code == 200
        order_data_1 = order_response_1.json()
        logging.info(f'Order response: {order_data_1}')
        logging.info('_________________________________________________________')

        # # Get the active book for the session
        response = await ac.get(f"/session/{session_id}/active_book/")
        assert response.status_code == 200
        logging.info(f'Active book response: {response.json()}')
        logging.info('$$$$$$$$$$$$$$$$$$$$$')
        # Trader2 places an ask order
        order_response_2 = await ac.post("/place_order/", json={
            "trader_id": trader_id_2,
            "session_id": session_id,
            "order_type": "ask",
            "quantity": 1,
            "price": 50.0
        })

        assert order_response_2.status_code == 200
        order_data_2 = order_response_2.json()
        logging.info(f'Order response: {order_data_2}')
        logging.info('_________________________________________________________')
        # # Get the active book for the session
        response = await ac.get(f"/session/{session_id}/active_book/")
        assert response.status_code == 200
        logging.info(f'Active book response: {response.json()}')

        # Step 5: Get the transaction history for the session
        # Get all transactions for the trader 1
        response = await ac.get(f"/trader/{trader_id_1}/transactions/")
        assert response.status_code == 200
        logging.info(f'Transactions response: {response.json()}')

        # Get all transactions for the trader 2
        response = await ac.get(f"/trader/{trader_id_2}/transactions/")
        assert response.status_code == 200
        logging.info(f'Transactions response: {response.json()}')
