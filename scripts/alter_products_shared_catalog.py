"""
Gerador de Recibo ENGEPOWER - Engenharia & Serviços
Layout fiel ao modelo original:
  - "RECIBO" e número do recibo na MESMA LINHA (RECIBO à esquerda, número à direita)
  - Dois campos/linhas abaixo (valor por extenso)
  - Original em cima, Duplicado em baixo, separados por linha de corte
"""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.colors import HexColor


def draw_receipt(c, x0, y0, w, h, numero="00016", label=""):
    laranja = HexColor('#E87010')
    vermelho = HexColor('#CC0000')
    cinza   = HexColor('#AAAAAA')
    cinza_l = HexColor('#DDDDDD')
    preto   = colors.black
    pad = 6 * mm

    # Borda geral
    c.setStrokeColor(cinza)
    c.setLineWidth(0.4)
    c.rect(x0, y0, w, h)

    # Caixa do logo
    box_w = 62 * mm
    box_h = 32 * mm
    box_x = x0 + w - box_w - pad
    box_y = y0 + h - box_h - pad

    c.setStrokeColor(cinza)
    c.setLineWidth(0.5)
    c.rect(box_x, box_y, box_w, box_h)

    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(laranja)
    c.drawCentredString(box_x + box_w / 2, box_y + box_h - 9 * mm, "ENGEPOWER")

    c.setFont("Helvetica", 6)
    c.setFillColor(laranja)
    c.drawCentredString(box_x + box_w / 2, box_y + box_h - 13 * mm, "ENGENHARIA & SERVICOS")

    c.setFont("Helvetica", 7)
    c.setFillColor(preto)
    dados = ["N.U.I.T: 401879307","Cell: +258 866414240","+258 83181818181","Lichinga - Niassa"]
    ty = box_y + box_h - 17 * mm
    for d in dados:
        c.drawCentredString(box_x + box_w / 2, ty, d)
        ty -= 3.5 * mm

    # RECIBO (esq) e NUMERO (dir) na mesma linha
    header_y = y0 + h - pad - 10 * mm
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(preto)
    c.drawString(x0 + pad, header_y, "RECIBO")

    c.setFont("Helvetica-Bold", 18)
    num_w = c.stringWidth(numero, "Helvetica-Bold", 18)
    c.setFillColor(vermelho)
    c.drawString(box_x - num_w - 4 * mm, header_y, numero)

    # Dois campos abaixo do cabeçalho
    campo_x = x0 + pad
    campo_w = box_x - x0 - pad * 2
    c.setStrokeColor(cinza)
    c.setLineWidth(0.4)
    c.line(campo_x, header_y - 8 * mm,  campo_x + campo_w, header_y - 8 * mm)
    c.line(campo_x, header_y - 16 * mm, campo_x + campo_w, header_y - 16 * mm)

    # Rotulo
    if label:
        c.setFont("Helvetica-Bold", 6.5)
        c.setFillColor(cinza)
        c.drawRightString(x0 + w - pad, y0 + h - pad - 1 * mm, label)

    # Separador
    sep_y = box_y - 4 * mm
    c.setStrokeColor(cinza_l)
    c.setLineWidth(0.4)
    c.line(x0, sep_y, x0 + w, sep_y)

    row_x = x0 + pad
    row_w = w - pad * 2
    cur_y = sep_y - 9 * mm

    def field_line(txt, lx, ly, lw, fs=8):
        c.setFont("Helvetica", fs)
        c.setFillColor(preto)
        c.drawString(lx, ly + 2, txt)
        tx = lx + c.stringWidth(txt, "Helvetica", fs) + 2 * mm
        c.setStrokeColor(cinza)
        c.setLineWidth(0.4)
        c.line(tx, ly, lx + lw, ly)

    def full_line(lx, ly, lw):
        c.setStrokeColor(cinza)
        c.setLineWidth(0.4)
        c.line(lx, ly, lx + lw, ly)

    # Recebemos do(s)
    field_line("Recebemos do(s) Exmo.(s) Sr.(es)", row_x, cur_y, row_w)

    # a quantia de [caixa] MT
    cur_y -= 10 * mm
    label_q = "a quantia de"
    lq_w = c.stringWidth(label_q, "Helvetica", 8)
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(row_x, cur_y + 2, label_q)
    val_x = row_x + lq_w + 4 * mm
    val_w = row_w - lq_w - 4 * mm - 10 * mm
    c.setStrokeColor(cinza)
    c.setLineWidth(0.5)
    c.rect(val_x, cur_y - 1 * mm, val_w, 7 * mm)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(preto)
    c.drawString(val_x + val_w + 2 * mm, cur_y + 2, "MT")

    # Proveniente no pagamento de
    cur_y -= 11 * mm
    field_line("Proveniente no pagamento de", row_x, cur_y, row_w)
    cur_y -= 7 * mm
    full_line(row_x, cur_y, row_w)

    # de que, passamos o presente recibo.
    cur_y -= 9 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(row_x, cur_y + 2, "de que, passamos o presente recibo.")

    # Linha: Numerario / Banco / Data
    cur_y -= 11 * mm
    c.setStrokeColor(cinza)
    c.setLineWidth(0.4)
    c.rect(row_x, cur_y - 0.5 * mm, 3.5 * mm, 3.5 * mm)
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(row_x + 5 * mm, cur_y + 2, "Numerario")

    banco_x = row_x + 36 * mm
    c.setStrokeColor(cinza)
    c.rect(banco_x, cur_y - 0.5 * mm, 3.5 * mm, 3.5 * mm)
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(banco_x + 5 * mm, cur_y + 2, "Banco")
    bw = c.stringWidth("Banco", "Helvetica", 8)
    banco_end = row_x + row_w * 0.52
    c.setStrokeColor(cinza)
    c.line(banco_x + 5 * mm + bw + 2 * mm, cur_y, banco_end, cur_y)

    date_x = banco_end + 3 * mm
    date_txt = "................, ........... de"
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(date_x, cur_y + 2, date_txt)
    dtw = c.stringWidth(date_txt, "Helvetica", 8)
    c.drawString(date_x + dtw + 2 * mm, cur_y + 2, "20")
    anow = c.stringWidth("20", "Helvetica", 8)
    c.setStrokeColor(cinza)
    c.line(date_x + dtw + 2 * mm + anow + 1 * mm, cur_y, row_x + row_w, cur_y)

    # Linha: Cheque N / Assinatura e Carimbo
    cur_y -= 11 * mm
    c.setStrokeColor(cinza)
    c.rect(row_x, cur_y - 0.5 * mm, 3.5 * mm, 3.5 * mm)
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(row_x + 5 * mm, cur_y + 2, "Cheque N")
    cqw = c.stringWidth("Cheque N", "Helvetica", 8)
    cheq_end = row_x + row_w * 0.38
    c.setStrokeColor(cinza)
    c.line(row_x + 5 * mm + cqw + 2 * mm, cur_y, cheq_end, cur_y)

    ass_x = cheq_end + 5 * mm
    c.setFont("Helvetica", 8)
    c.setFillColor(preto)
    c.drawString(ass_x, cur_y + 2, "Assinatura e Carimbo")
    aw = c.stringWidth("Assinatura e Carimbo", "Helvetica", 8)
    c.setStrokeColor(cinza)
    c.line(ass_x + aw + 2 * mm, cur_y, row_x + row_w, cur_y)

    # Rodape
    c.setFont("Helvetica", 5)
    c.setFillColor(HexColor('#999999'))
    c.drawString(x0 + pad, y0 + 3 * mm,
        "Top Grafica, Lda. - Av. Eduardo Mondlane 203, Autorizacao nr 025/MFF - TIP/99 - NUIT: 400061084")


def gerar_recibo_pdf(output_path="recibo_engepower.pdf", numero_recibo="00016"):
    page_w, page_h = A4
    margem = 8 * mm
    gap    = 7 * mm
    rec_w = page_w - 2 * margem
    rec_h = (page_h - 2 * margem - gap) / 2

    c = canvas.Canvas(output_path, pagesize=A4)
    c.setTitle(f"Recibo ENGEPOWER {numero_recibo}")

    # ORIGINAL
    draw_receipt(c, margem, margem + rec_h + gap, rec_w, rec_h, numero_recibo, "")

    # Linha de corte
    cut_y = margem + rec_h + gap / 2
    c.setStrokeColor(HexColor('#BBBBBB'))
    c.setLineWidth(0.5)
    c.setDash([4, 4])
    c.line(margem + 5 * mm, cut_y, page_w - margem, cut_y)
    c.setDash([])
    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor('#BBBBBB'))
    c.drawString(margem, cut_y - 1.5 * mm, "X")

    # DUPLICADO
    draw_receipt(c, margem, margem, rec_w, rec_h, numero_recibo, "DUPLICADO")

    c.save()
    print(f"Recibo gerado: {output_path}")


if __name__ == "__main__":
    gerar_recibo_pdf(output_path="recibo_engepower.pdf", numero_recibo="00016")