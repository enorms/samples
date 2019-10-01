# Copyright 2019 Eric Norman
# This code select sthe best (cheapest) shipping quote by taking in package information
# then calling and sorting from multiple provider APIs.
# The approach was to call in all available quotes once from each API
# then sort internally in the program as needed;
# it was the first time I had built something for unknown length
# and required lots of mods for practical use.
# Using the giant rate on ln 196 now looks like a hack; 
# it was one of the first accumulators I had written, before I knew the concept.


import sys, os, pprint, json, csv
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from pprint import pprint
import easypost
from pack_cli.api_keys.secrets import easypost_test_api_key, easypost_production_api_key
from pack_cli.conversions import convert_ounces_to_grams
from pack_cli.easypost_functions import \
    ep_generate_address_object, \
    ep_convert_address_object_to_dict, \
    ep_generate_parcel_object
from pack_cli.shipstation_functions import \
    get_quotes_for_carrier, \
    ss_generate_address_object_from_dict, \
    ss_get_fedex_shipping_label

# easypost.api_key = easypost_test_api_key
easypost.api_key = easypost_production_api_key
CSV_PATH = 'csv/orders_export.csv'


def read_order_csv_and_return_to_address_and_items(CSV_PATH, ORDER_TO_PULL=None):
    """
    Very limited read of order data for quoting and shipping label creation.

    Limitation examples: 
        cannot handle multi-item (row) order
        orders_to_filter must be done manually
        does not pull in 'Shipping Company'; because not enough fields in EasyPost
        does not assign internal unique ID

    Args:
        CSV_PATH(str): repo relative path to file to read
        ORDER_TO_PULL(str): which order to operate on

    Returns:
        to_address_dict(dict)
    """
    to_address_dict = {}
    with open(CSV_PATH) as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['Name'].casefold() == ORDER_TO_PULL.casefold():
                to_address_dict['customer_order_id'] = row['Name']
                to_address_dict['sales_platform__order_id'] = row['Id']
                to_address_dict['name'] = row['Shipping Name']
                to_address_dict['street1'] = row['Shipping Street']
                to_address_dict['street2'] = row['Shipping Address2']
                to_address_dict['city'] = row['Shipping City']
                to_address_dict['state'] = row['Shipping Province']
                to_address_dict['postal_code'] = row['Shipping Zip']
                to_address_dict['country'] = row['Shipping Country']
                to_address_dict['phone'] = row['Shipping Phone']
    return to_address_dict


def pull_and_calculate_customer_facing_quote(from_address_dict, to_address_dict, parcel_dict_oz, excluded=[]):
    """
    Assumes
        item is enflux large item

    Args:
        from_address_dict(dict): where the shipment is from (not as object)
        to_address_dict(dict): where the shipment is going (not as object)
        parcel_dict_oz(dict): dimensions in inches, weight with unit -oz for now because of EP
            includes description(str): to print for customer
        excluded(list of str): service or carrier
            example: if customer does not want FedEx, put "['fedex']"

    Returns:
        tuple[0] best_quote(dict): raw best quote, before platform fees
        tuple[1] comparison_quote(dict): raw comparison
        tuple[2] ep_shipment(EP Object): can be used to purchase ep_shipment
        tuple[3] ss_quotes(dict): list of shipstation quote dicts under the "ss_rates" key. Example:
            {
                'ss_rates':[
                    {
                        'carrier': 'fedex',
                        'service': 'ground,
                        'rate': 8.50,
                        'est_delivery_days': None # Always none for SS
                    },
                    ...
                ]
            }
    """
    # use all lower case
    excluded = ['parcelselect', 'first', 'fedex_smartpost_parcel_select']
    EXCLUDED_USPS = ['first']
    # try a dict with key of carrier code, value of service code
    excluded_dict = {
        'usps': ['parcelselect', 'first'],
        'fedex': ['fedex_smartpost_parcel_select']
        }

    # FLAT RATES
    flat_rates = {
        'FlatRateEnvelope': 
            {'length': 12.5,
            'width': 9.5,
            'height': 0.1,
            'units': 'inches'},
        'FlatRateLegalEnvelope': 
            {'length': 15,
            'width': 9.5,
            'height': 0.1,
            'units': 'inches'},
    }


    # EasyPost
    # Use EasyPost verified address + residential flag for all quoting / label purchase
    try:
        predefined_package = parcel_dict_oz['predefined_package']
        ep_parcel_object = easypost.Parcel.create(
            predefined_package = predefined_package,
            weight = parcel_dict_oz['weight_oz'],
        )
    except KeyError:
        ep_parcel_object = ep_generate_parcel_object(parcel_dict_oz)
    #TODO: allow flatrateenvelopes
    ep_from_address_object = ep_generate_address_object(
        from_address_dict['name'],
        from_address_dict['street1'],
        from_address_dict['country'],
        from_address_dict['postal_code'],
        from_address_dict['street2'],
        from_address_dict['city'], 
        from_address_dict['state'],
        from_address_dict['phone'],
        from_address_dict['company'],
        )
    ep_to_address_object = ep_generate_address_object(
        to_address_dict['name'],
        to_address_dict['street1'], 
        to_address_dict['country'],
        to_address_dict['postal_code'],
        to_address_dict['street2'],
        to_address_dict['city'],
        to_address_dict['state'],
        to_address_dict['phone'],
        to_address_dict['company'],
        )
    # NOTE: DHL requires value in CustomsItem
    customs_item_object = easypost.CustomsItem.create(
        description='E-fabric',
        quantity=1,
        value=1,
        weight=16,
        hs_tariff_number='3926.20',
        origin_country='US'
    )
    customs_info_object = easypost.CustomsInfo.create(
        eel_pfc='NOEEI 30.37(a)',
        contents_type='sample',
        customs_certify=True,
        customs_signer='NAME',
        restriction_type='none',
        customs_items=[customs_item_object]
    )
    ep_shipment = easypost.Shipment.create(
        from_address = ep_from_address_object,
        to_address = ep_to_address_object,
        parcel = ep_parcel_object,
        customs_info = customs_info_object
    )
    try:
        if ep_shipment.messages != None and len(ep_shipment.messages) != 0:
            if 'Unable to retrieve DHLExpress rates for US domestic'.casefold() in str(ep_shipment.messages).casefold():
                pass
            else:
                print(ep_shipment.messages)
    except KeyError:
        print('No Messages in Shipment')

    # Prevent error: "Unable to retrieve DHLExpress rates for US domestic ep_shipments."
    if ep_to_address_object.country == 'US' and ep_from_address_object.country == 'US':
        excluded.append('dhl')
    else:
        pass

    # Get our rate
    # Quote from easypost, just the expected DHL best quote
    best_quote = {}
    # Hard Code expensive rate and search for cheaper; allows for UPS
    best_quote['rate'] = 99999999
    for rate in ep_shipment.rates:
        carrier = rate.carrier.casefold()
        service = rate.service.casefold()
        if carrier in str(excluded_dict.keys()) and service in excluded_dict[carrier]:
            continue
        # if rate.carrier.casefold() == 'usps' and rate.service.casefold() in EXCLUDED_USPS:
        #     continue
        # elif rate.service in str(excluded) or rate.carrier in str(excluded):
        #     continue
        elif carrier == 'usps' and (rate.rate != rate.list_rate):
            # 2019-05-31 Ran into USPS quoting bug where priority mail was quoted way too 
            # low by multiple providers. The "list_rate" was correct, though, and equal
            # to what "rate" should have been. So testing for that here and healing, 
            # but giving a warning in the service string so you know it happened
            print("USPS quote appears to be wrong. Rate is %s, but list rate is %s. They should be equal."%(str(rate.rate),str(rate.list_rate)))
            max_rate = max(float(rate.rate),float(rate.list_rate))
            rate.rate = '%.2f'%(max_rate)
            rate.service += ' *** LIST RATE (CPP, corrected) ***'
        elif float(rate.rate) < best_quote['rate']:
            best_quote['rate'] = float(rate.rate)
            best_quote['service'] = rate.service
            best_quote['carrier'] = rate.carrier
            best_quote['source'] = 'easypost/' + rate.carrier_account_id.lower()
            best_quote['quote_id'] = rate.id
            best_quote['est_delivery_days'] = rate.est_delivery_days
        else:
            pass
    
    # Quote from SS
    # Use corrected address from EasyPost
    if 'fedex' in excluded:
        pass
    else:
        customer_name = 'Product Jump'
        to_address_dict = ep_convert_address_object_to_dict(ep_to_address_object)
        to_address_ss_object = ss_generate_address_object_from_dict(to_address_dict)
        try:
            predefined_package = parcel_dict_oz['predefined_package']
            dimensions = {
                'length': flat_rates[predefined_package]['length'],
                'width': flat_rates[predefined_package]['width'],
                'height': flat_rates[predefined_package]['height'],
                'units': flat_rates[predefined_package]['units']
            }
        except KeyError:
            ep_parcel_object = ep_generate_parcel_object(parcel_dict_oz)
            dimensions = {
                'length': parcel_dict_oz['length'],
                'width': parcel_dict_oz['width'],
                'height': parcel_dict_oz['height'],
                'units': 'inches'
            }
        weight_grams = convert_ounces_to_grams(parcel_dict_oz['weight_oz'])
        carrierCode = 'fedex'
        # USPS: SIGNATURE: does pass to Shipment, does not add cost
        # USPS: signature, ADULT_SIGNATURE does not register
        # USPS: INDIRECT_SIGNATURE is error
        delivery_confirmation='SIGNATURE'   
        print("Hard coded: delivery_confirmation='SIGNATURE'")  
        from_address_dict = ep_convert_address_object_to_dict(ep_from_address_object)
        from_address_ss_object = ss_generate_address_object_from_dict(from_address_dict)
        serviceCode = None
        ss_quotes = get_quotes_for_carrier(
            customer_name, to_address_ss_object, weight_grams, carrierCode,
            dimensions, delivery_confirmation, from_address_ss_object, serviceCode)
        
        ss_quotes_to_return = {'ss_rates': []}
        # Compare to previous and choose best one:
        # NOTE: assumes there is an existing best quote
        for q in ss_quotes:
            carrier = q['carrierCode'].casefold()
            service = q['serviceCode'].casefold()
            q['total_cost'] = q['shipmentCost'] + q['otherCost']
            ss_quotes_to_return['ss_rates'].append(
                {
                    'carrier': q['carrierCode'],
                    'service': q['serviceName'],
                    'rate': round(q['total_cost'],2),
                    'est_delivery_days': None
                }
            )
            if carrier in str(excluded_dict.keys()) and service in excluded_dict[carrier]:
                continue
            else:
                q['total_cost'] = q['shipmentCost'] + q['otherCost']
                if q['total_cost'] >= best_quote['rate']:
                    continue
                else:
                    best_quote['rate'] = q['total_cost']
                    best_quote['service'] = q['serviceName']
                    best_quote['carrier'] = q['carrierCode']
                    best_quote['source'] = 'shipstation/' + customer_name.lower()
                    best_quote['quote_id'] = None

    # Get comparison rate; hard-coded to USPS Priority and EasyPost
    # TODO: make INSURANCE_ESTIMATE dynamic
    try:
        INSURANCE_ESTIMATE = parcel_dict_oz['insurance_value']
        print('Insurance value: ', INSURANCE_ESTIMATE)
    except KeyError:
        INSURANCE_ESTIMATE = 0
        print('Insurance value: ', INSURANCE_ESTIMATE)        
    comparison_quote = {}
    for rate in ep_shipment.rates:
        if rate.carrier.casefold() == 'USPS'.casefold() and "Priority".casefold() in rate.service.casefold():
            comparison_quote['rate'] = rate.rate
            comparison_quote['rate'] = float(comparison_quote['rate']) + INSURANCE_ESTIMATE
            comparison_quote['source'] = rate.carrier_account_id.lower()
            comparison_quote['est_delivery_days'] = rate.est_delivery_days
            if rate.carrier.casefold() in rate.service.casefold():
                comparison_quote['service'] = rate.service
            else:
                comparison_quote['service'] = rate.carrier + ' ' + rate.service     
    # pprint(comparison_quote)    # Debug

    # Then apply the additional fees, purchases
    # Only apply if our quote is better
    if comparison_quote['service'].casefold() == best_quote['service'].casefold() and comparison_quote['carrier'].casefold() == best_quote['carrier'].casefold():
        print('Skipping: best_quote = comparison_quote')
        return({},{})
    # Skip quotes that are not cheaper
    elif comparison_quote['rate'] < best_quote['rate']:
        print("Skipping: best_quote > comparison_quote", from_address_dict['city'], to_address_dict['city'], parcel_dict_oz['description'])
        return({},{})
    else:        
        # Add header info at last stage of skipping here
        best_quote['from'] = from_address_dict # after verification
        best_quote['to'] = to_address_dict     # after verification
        best_quote['parcel'] = parcel_dict_oz   # includes item description
    return (best_quote, comparison_quote, ep_shipment, ss_quotes_to_return)


def calculate_accounting_info_from_customer_facing_quote(best_quote, comparison_quote):
    """Add platform fee and adjust for things like insurance.
    Print summary and useful info.

    #TODO: pull out: insurance_cost, platform_fee, shipping_cost

    Args:
        best_quote(dict): rate, service, carrier, source(quote platform / account)
        comparison_quote(dict): 
    Returns:
        tuple[0] present_to_customer(dict): relavant info
        tuple[1] internal_accounting_info(dict): relavant info

    NOTES:
        SS Insurance:
            INSURANCE RATES
                USPS Domestic: $0.99 per $100 of coverage
                FedEx SmartPost Domestic: $0.99 per $100 of coverage
                Canada Post Domestic: $0.99 per $100 of coverage
                All Other Carriers (FedEx/UPS/DHL Express) Domestic: $0.79 per $100 of coverage
                International: $1.25 per $100 of coverage
            LIMTS
                This insurance covers up to $999.99 for USPS First Class Mail shipments, $1,000.00 for FedEx SmartPost,
                UPS Mail Innovations, and DHL Global Mail shipments, and $10,000.00 per package for other USPS,
                FedEx, UPS, and DHL shipments. Maximum coverage per conveyance is $100,000.00. The coverage is also
                limited to $5,000.00 for USPS Priority Mail International shipments
                (non- Priority Mail Express International). Mobile phones (cell phones, smart phones, etc) are limited 
                 $5,000.00 of coverage per package and $25,000.00 per conveyance.
        """
    
    PLATFORM_FEE_PERCENT =?? #[REDACTED]
    value_insurance = 0 #    #TODO: pass this
    EP_INSURANCE_COST = value_insurance * .01   # easypost rate
    SS_INSURANCE_COST = 0   # unknown, may have shipsurance #TODO: modify call to add this
    # our_quote['rate'] = float(our_quote['rate'] + INSURANCE_COST) #TODO, apply rules based on provider
    # our_quote['insurance_coverage'] = ITEM_VALUE

    our_quote = {}
    our_quote['rate'] = float(best_quote['rate'] / (1 - PLATFORM_FEE_PERCENT))

    # Stop if coomparison is worse
    if comparison_quote['rate'] < our_quote['rate']:    #TODO: add threshholds
        print("Skipping: our_quote['rate'] > comparison_quote['rate']")
        return({},{})

    # Add header info at last stage of skipping here
    present_to_customer = {}
    present_to_customer['from'] = best_quote['from']
    present_to_customer['to'] = best_quote['to']
    present_to_customer['parcel'] = best_quote['parcel'] # includes item description
    if best_quote['carrier'].casefold() in best_quote['service'].casefold():
        present_to_customer['our_service'] = best_quote['service']
    else:
        present_to_customer['our_service'] = best_quote['carrier'] + ' ' + best_quote['service']
    present_to_customer['our_quote'] = round(our_quote['rate'], 2)
    present_to_customer['comparison_quote'] = round(comparison_quote['rate'], 2)
    present_to_customer['comparison_service'] = comparison_quote['service']
    present_to_customer['savings'] = round(present_to_customer['comparison_quote'] - present_to_customer['our_quote'], 2)
    present_to_customer['savings_percent'] = round(100 * present_to_customer['savings'] / present_to_customer['comparison_quote'], 0)
    # pprint(present_to_customer)     # Debug

    SHIPPING_ACCOUNT_HOLDER_RATE = ?? #[REDACTED]
    internal_accounting_info = {}
    # internal_accounting_info['customer name'] = ''
    internal_accounting_info['order timestamp'] = '2019-05-28'  #TODO: dynamic, or order ID
    internal_accounting_info['platform fee'] = PLATFORM_FEE_PERCENT
    internal_accounting_info['cost shipping'] = round(best_quote['rate'], 2)
    internal_accounting_info['cost insurance'] = 0  #TODO: update with insurance update
    internal_accounting_info['cost account holder'] = round(best_quote['rate'], 2)
    internal_accounting_info['shipping account holder fee'] = round(
        best_quote['rate'] * SHIPPING_ACCOUNT_HOLDER_RATE, 2)
    internal_accounting_info['shipping account holder payable'] = round(
        internal_accounting_info['shipping account holder fee'] + internal_accounting_info['cost shipping'], 2)
    internal_accounting_info['customer receivable'] = round(our_quote['rate'], 2)
    internal_accounting_info['cost of goods'] = round(internal_accounting_info['cost shipping'] + internal_accounting_info['cost insurance'], 2)
    internal_accounting_info['gross'] = internal_accounting_info['customer receivable'] - internal_accounting_info['cost of goods']
    internal_accounting_info['gross margin'] = round(internal_accounting_info['gross'] / internal_accounting_info['customer receivable'], 2)
    # pprint(internal_accounting_info)    # Debug

    # TODO: return info needed for label to automate printing
    # label_creation_info = {}
    # carrier
    # service_code
    # from_address_dict
    # to_address_dict
    # weight_oz
    # dimensions
    # insurance_info
    # customs_stuff
    # delivery_confirmation
    # spend_money



    # return (present_to_customer, internal_accounting_info, label_creation_info)
    return (present_to_customer, internal_accounting_info)


# TODO: Write function to create shipping label
# def create_shipping_label(.....):
#     .....


def main(from_address_dict, to_address_dict, parcel_dict_oz):
    """ Quote for one order.
    Provide comparison and savings.
    
    Args:
        from/to_address_dict(dict): makes test of this function easier"""

    r = pull_and_calculate_customer_facing_quote(from_address_dict, to_address_dict, parcel_dict_oz)

    # print("******* r *******")
    # pprint(r)
    present_to_customer = r[0]
    print("******* present to customer *******")
    pprint(present_to_customer)
    internal_accounting_info = r[1]
    print("******* internal accounting info *******")
    pprint(internal_accounting_info)
    print("******* Alternatives *******")
    for rate in r[2].rates:
        print('%s %s: $%s. Est delivery in %s days.'%(rate.carrier, rate.service, str(rate.rate), str(rate.est_delivery_days)))
    for rate in r[3]['ss_rates']:
        # Carrier name is included in the service field by shipstation
        print('%s: $%s. Est delivery in %s days.'%(rate['service'], str(rate['rate']), str(rate['est_delivery_days'])))
    return

    
if __name__ == '__main__':
    
    from_address_dict = {}
    to_address_dict = {}

    # Enflux Main Return Address
    from_address_dict['name'] = "Name"
    from_address_dict['company'] = "company"
    from_address_dict['street1'] = "street1"
    from_address_dict['street2'] =""
    from_address_dict['city'] = "Costa Mesa"
    from_address_dict['state'] = 'CA'
    from_address_dict['postal_code'] = '92627'
    from_address_dict['country'] = 'US'
    from_address_dict['residential'] = True # not confirmed or automated

    # To address for the order
    to_address_dict['name'] = "name"
    to_address_dict['street1'] = "street1"
    to_address_dict['street2'] = ""
    to_address_dict['city'] = ""
    to_address_dict['state'] = ""
    to_address_dict['postal_code'] = "81675"
    to_address_dict['country'] = "Germany"
    to_address_dict['phone'] = ""

    # Parcel dimensions and weight (oz)
    parcel_dict_oz = {
        'length': 12.4,  # inches
        'width': 9.4,    #inches
        'height': .5,
        'weight_oz': 16   # ounces
    }
    main(from_address_dict, to_address_dict, parcel_dict_oz)
