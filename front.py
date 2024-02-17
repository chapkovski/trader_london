import streamlit as st
import requests
import uuid

def register_trader(trader_id):
    """Register the trader with the backend."""
    response = requests.post('http://localhost:8000/traders/register', json={'trader_id': trader_id})
    if response.ok:
        st.success('Trader registered successfully')
    else:
        st.error(f'Failed to register trader: {response.text}')

if 'trader_id' not in st.session_state:
    st.session_state.trader_id = str(uuid.uuid4())
    # Register the trader as soon as the ID is generated
    register_trader(st.session_state.trader_id)

trader_id = st.session_state.trader_id

st.title('Trading Platform Interface')
st.write(f"Your session's unique trader ID: {trader_id}")

# Display order book
def fetch_order_book():
    response = requests.get('http://localhost:8000/orders/')
    if response.ok:
        orders = response.json()['orders']
        return orders
    else:
        st.error(f'Failed to fetch order book: {response.text}')
        return []

# Place and cancel order UI
amount = st.number_input('Amount:')
price = st.number_input('Price:')
order_type = st.selectbox('Order Type', ['BUY', 'SELL'])

if st.button('Place Order'):
    # Example of including trader_id in a request
    order_data = {'amount': amount, 'price': price, 'order_type': order_type, 'trader_id': trader_id}
    response = requests.post('http://localhost:8000/send-order/', json=order_data)
    if response.ok:
        st.success('Order placed successfully')
        order_book = fetch_order_book()
        st.write(order_book)
    else:
        st.error('Failed to place order')

order_id_to_cancel = st.text_input("Order ID to Cancel:")

if st.button('Cancel Order'):
    cancel_data = {'order_id': order_id_to_cancel}
    response = requests.post('http://localhost:8000/cancel-order/', json=cancel_data)
    if response.ok:
        st.success('Cancel order request sent')
    else:
        st.error('Failed to send cancel order request')