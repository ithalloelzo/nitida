<div align="center">

# ✨ Nítida

### Ampliador de imagens com IA — 100% local, gratuito e sem limites

Faça o mesmo tipo de trabalho de serviços como o **bigjpg**, sem enviar nenhuma imagem para a internet.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Plataforma](https://img.shields.io/badge/Windows-11-0078D4?logo=windows11&logoColor=white)
![GPU](https://img.shields.io/badge/GPU-Vulkan-AC162C?logo=vulkan&logoColor=white)
![Dependências](https://img.shields.io/badge/depend%C3%AAncias-zero-brightgreen)
![Engine](https://img.shields.io/badge/engine-waifu2x--ncnn--vulkan-orange)

</div>

---

## 📖 Sobre

**Nítida** é um ampliador de imagens com inteligência artificial que roda inteiramente no seu computador.
Suas imagens nunca saem da sua máquina, não há cadastro, cota nem limite de uso.

> 🔒 **Privacidade total** · 🚀 **Acelerado por GPU** · 📦 **Sem pacotes extras**

---

## 🎯 Funcionalidades

| Recurso | Detalhes |
|---|---|
| 🎨 **Tipo de imagem** | **Desenho/anime** (modelo `cunet`) ou **Foto** (modelo `upconv_7_photo`) |
| 🔍 **Ampliação** | `2x` · `4x` · `8x` · `16x` |
| 🧹 **Redução de ruído** | Nenhuma · Baixa · Média · Alta · Máxima (nos dois tipos de imagem) |
| 💾 **Formato de saída** | Igual ao original · WebP · JPG · PNG |
| 📂 **Várias imagens** | Arrastar e soltar, fila com progresso e botão **Ampliar todas** |
| 🆚 **Antes/depois** | Comparação com controle deslizante |
| 🕘 **Histórico** | Salvo em disco, com paginação, seleção, download em ZIP e exclusão |
| ⚡ **GPU com fallback** | Usa Vulkan (AMD, NVIDIA ou Intel); se a GPU falhar, tenta de novo na CPU |

---

## 🚀 Começando

### Pré-requisitos

- **Python 3.10** ou mais novo
- Nenhum pacote extra (`pip install` não é necessário)

### Execução

**Opção 1 — mais fácil:** dê dois cliques em `iniciar.bat`.

**Opção 2 — pelo terminal:**

```bash
python server.py
```

Depois é só:

1. O navegador abre em **http://127.0.0.1:8765**
2. Escolha as opções (tipo, ampliação, ruído e formato)
3. Solte as imagens e clique em **Ampliar** 🎉

---

## 🗂️ Estrutura do projeto

```text
nitida_win11/
├── server.py          # servidor HTTP local + fila de processamento
├── iniciar.bat        # atalho para iniciar no Windows
├── static/            # interface (HTML, CSS, JS)
├── engine/            # waifu2x-ncnn-vulkan 20250915 (MIT)
└── data/
    ├── uploads/       # imagens originais enviadas
    ├── results/       # imagens ampliadas
    └── history.json   # histórico
```

---

## ⚙️ Configuração

Variáveis de ambiente opcionais:

| Variável | Descrição | Padrão |
|---|---|---|
| `NITIDA_PORT` | Porta do servidor | `8765` |
| `NITIDA_ENGINE` | Caminho de outro executável compatível com a CLI do waifu2x-ncnn-vulkan | `engine/` incluído |

Exemplo (PowerShell):

```powershell
$env:NITIDA_PORT = "9000"
python server.py
```

---

## ⚠️ Limites

| Item | Limite |
|---|---|
| Arquivo de entrada | até **50 MB** · JPG / PNG / WebP |
| Saída em **WebP** | até **16.383 px** por lado (limite do formato) |
| Saída em **JPG/PNG** | até **32.000 px** por lado e **400 megapixels** no total |

---

## 🙏 Créditos

Este projeto usa o [**waifu2x-ncnn-vulkan**](https://github.com/nihui/waifu2x-ncnn-vulkan) de *nihui* (licença MIT) como motor de ampliação.

---

<div align="center">

Feito com 💙 para quem quer imagens nítidas sem abrir mão da privacidade.

⭐ Se o projeto foi útil, deixe uma estrela no repositório!

</div>
