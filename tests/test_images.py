"""商品图片提取：白名单、名称配对、去重（connectors/images.py）。"""

from cooker.connectors.images import allowed, extract_images

LK = frozenset({"luckincoffeecdn.com"})


def test_only_https_official_hosts():
    assert allowed("https://img06.luckincoffeecdn.com/a.png", LK)
    assert not allowed("http://img06.luckincoffeecdn.com/a.png", LK)        # 非 https
    assert not allowed("https://evil.example/luckincoffeecdn.com.png", LK)  # 域名不在白名单
    assert not allowed("https://luckincoffeecdn.com.evil.example/a.png", LK)  # 后缀伪装
    assert not allowed(None, LK)


def test_pairs_name_with_image_and_dedupes():
    payload = {"data": [
        {"productName": "生椰拿铁（原创）", "pictureUrl": "https://img06.luckincoffeecdn.com/1.png"},
        {"productName": "重复", "pictureUrl": "https://img06.luckincoffeecdn.com/1.png"},
        {"productName": "外链", "pictureUrl": "https://tracker.example/p.gif"},
        {"pictureUrl": "https://img06.luckincoffeecdn.com/2.png"},  # 没有名称：无法和回答对应，跳过
        {"meals": {"920215": {"name": "橙C美式", "image": "https://img06.luckincoffeecdn.com/3.png"}}},
    ]}
    assert extract_images(payload, LK) == [
        {"name": "生椰拿铁（原创）", "url": "https://img06.luckincoffeecdn.com/1.png"},
        {"name": "橙C美式", "url": "https://img06.luckincoffeecdn.com/3.png"},
    ]
    assert extract_images(payload, frozenset()) == []  # 未声明图床的连接器不提取


def test_strip_images_keeps_payment_qr():
    from cooker.connectors.images import strip_images

    payload = {"data": {"orderId": "LK1", "payOrderQrCodeUrl": "https://pay.example/qr.png",
                        "items": [{"name": "生椰拿铁", "pictureUrl": "https://img06.luckincoffeecdn.com/1.png", "price": 20}]}}
    assert strip_images(payload) == {"data": {"orderId": "LK1", "payOrderQrCodeUrl": "https://pay.example/qr.png",
                                              "items": [{"name": "生椰拿铁", "price": 20}]}}
